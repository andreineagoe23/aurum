//+------------------------------------------------------------------+
//| AurumReference.mq5                                               |
//| Phase 3 parity EA: the MT5 Strategy Tester twin of               |
//| aurum.strategies.reference.FixedTimeReference (variant m5) under  |
//| configs/strategies/reference_fixed_time.yaml and sessions.yaml.  |
//|                                                                  |
//| NOT a research strategy. It has no edge by design. Tester only.  |
//|                                                                  |
//| Rules (all times UTC; Aurum decides at M5 bar closes):           |
//|  * At 08:00 UTC: BUY, stop = M5 bid close - 1000 points,         |
//|    target = close + 1500 points, close after 120 minutes.        |
//|  * At 14:30 UTC: SELL, stop = close + 3000 points,               |
//|    target = close - 4500 points, no time limit.                  |
//|  * "close" = bid close of the M5 bar that ended at the entry     |
//|    time; no entry if that bar is missing (data gap).             |
//|  * One position at a time; market orders fill on the first tick  |
//|    of the new bar (Aurum with latency 0: first quote at or after |
//|    the decision instant).                                        |
//|  * Force-flat at 20:00 Europe/London on the first tick at or     |
//|    after it. (The daily-break cap, 16:59 New York - 15 min, is   |
//|    always later; early-close days are NOT modelled: keep them    |
//|    out of the test window.)                                      |
//|                                                                  |
//| Server clock: Fusion = New York wall clock + 7h (ADR-0004).      |
//|                                                                  |
//| Output: when the test ends, every deal of this EA is written to  |
//| <Common>\Files\AurumReference_deals.csv (Terminal common folder: |
//| %APPDATA%\MetaQuotes\Terminal\Common\Files). Times are raw       |
//| server milliseconds; Aurum converts them (aurum backtest parity).|
//+------------------------------------------------------------------+
#property copyright "Aurum (research, demo only)"
#property version   "1.00"

#include <Trade\Trade.mqh>

input double InpLots             = 0.10;    // backtest.yaml fixed_volume_lots
input long   InpMagic            = 7020261; // broker.yaml magic_number
input int    InpBuyMinuteUtc     = 480;     // 08:00 UTC
input int    InpBuyStopPoints    = 1000;
input int    InpBuyTargetPoints  = 1500;
input int    InpBuyMaxHoldMin    = 120;
input int    InpSellMinuteUtc    = 870;     // 14:30 UTC
input int    InpSellStopPoints   = 3000;
input int    InpSellTargetPoints = 4500;
input int    InpForceFlatLondon  = 1200;    // 20:00 Europe/London, minutes after midnight
input int    InpServerShiftHours = 7;       // server = New York + 7h
input string InpOutFile          = "AurumReference_deals.csv";

CTrade   g_trade;
datetime g_lastBar      = 0;
long     g_deadlineMs   = 0;   // UTC ms; 0 = no time limit
long     g_flatMs       = 0;   // UTC ms of today's force-flat
ulong    g_reasonPos[];        // positions the EA closed itself ...
string   g_reasonTxt[];        // ... and why (TIMEOUT / FORCE_FLAT)

//--- calendar helpers -------------------------------------------------
datetime MakeDate(int y, int m, int d, int hh = 0, int mi = 0)
  {
   MqlDateTime s;
   ZeroMemory(s);
   s.year = y; s.mon = m; s.day = d; s.hour = hh; s.min = mi;
   return StructToTime(s);
  }

int DayOfWeek(datetime t)
  {
   MqlDateTime s;
   TimeToStruct(t, s);
   return s.day_of_week; // 0 = Sunday
  }

// n-th Sunday (1-based) of a month, 00:00.
datetime NthSunday(int y, int m, int n)
  {
   datetime first = MakeDate(y, m, 1);
   int offset = (7 - DayOfWeek(first)) % 7;
   return first + (offset + 7 * (n - 1)) * 86400;
  }

datetime LastSunday(int y, int m)
  {
   int ny = (m == 12) ? y + 1 : y;
   int nm = (m == 12) ? 1 : m + 1;
   datetime last = MakeDate(ny, nm, 1) - 86400;
   return last - DayOfWeek(last) * 86400;
  }

// US daylight time, judged on New York wall clock: 2nd Sunday March 02:00 .. 1st Sunday Nov 02:00.
bool UsDst(datetime nyLocal)
  {
   MqlDateTime s;
   TimeToStruct(nyLocal, s);
   datetime start = NthSunday(s.year, 3, 2) + 2 * 3600;
   datetime end   = NthSunday(s.year, 11, 1) + 2 * 3600;
   return nyLocal >= start && nyLocal < end;
  }

// British Summer Time, judged in UTC: last Sunday March 01:00 .. last Sunday October 01:00.
bool UkDst(datetime utc)
  {
   MqlDateTime s;
   TimeToStruct(utc, s);
   datetime start = LastSunday(s.year, 3) + 3600;
   datetime end   = LastSunday(s.year, 10) + 3600;
   return utc >= start && utc < end;
  }

// Seconds to subtract from server time to get UTC.
long ServerOffset(datetime server)
  {
   datetime ny = server - InpServerShiftHours * 3600;
   return (long)InpServerShiftHours * 3600 + (UsDst(ny) ? -4 : -5) * 3600;
  }

long ServerMsToUtcMs(long serverMs)
  {
   return serverMs - ServerOffset((datetime)(serverMs / 1000)) * 1000;
  }

// UTC ms of the force-flat instant on the UTC date of utcMs.
long ForceFlatMs(long utcMs)
  {
   datetime day = (datetime)((utcMs / 1000) / 86400 * 86400);
   datetime asUtc = day + InpForceFlatLondon * 60;
   datetime flat = asUtc - (UkDst(asUtc - 3600) ? 3600 : 0);
   return (long)flat * 1000;
  }

//--- bookkeeping ------------------------------------------------------
bool OwnPosition(ulong &ticket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t != 0 && PositionGetInteger(POSITION_MAGIC) == InpMagic
         && PositionGetString(POSITION_SYMBOL) == _Symbol)
        {
         ticket = t;
         return true;
        }
     }
   return false;
  }

void CloseWithReason(ulong ticket, string reason)
  {
   if(g_trade.PositionClose(ticket))
     {
      int n = ArraySize(g_reasonPos);
      ArrayResize(g_reasonPos, n + 1);
      ArrayResize(g_reasonTxt, n + 1);
      g_reasonPos[n] = ticket;
      g_reasonTxt[n] = reason;
     }
   else
      PrintFormat("close %s failed: %d %s", reason, g_trade.ResultRetcode(), g_trade.ResultComment());
  }

string ReasonFor(ulong position)
  {
   for(int i = 0; i < ArraySize(g_reasonPos); i++)
      if(g_reasonPos[i] == position)
         return g_reasonTxt[i];
   return "";
  }

//--- EA events --------------------------------------------------------
int OnInit()
  {
   if(!MQLInfoInteger(MQL_TESTER))
     {
      Print("AurumReference runs in the Strategy Tester only");
      return INIT_FAILED;
     }
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(50);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   return INIT_SUCCEEDED;
  }

void OnTick()
  {
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return;
   long nowMs = ServerMsToUtcMs(tick.time_msc);

   // 1. Exits the EA owns (stop and target are broker-side).
   ulong ticket;
   if(OwnPosition(ticket))
     {
      if(g_flatMs > 0 && nowMs >= g_flatMs)
         CloseWithReason(ticket, "FORCE_FLAT");
      else if(g_deadlineMs > 0 && nowMs >= g_deadlineMs)
         CloseWithReason(ticket, "TIMEOUT");
     }

   // 2. Entries, at the first tick of a new M5 bar.
   datetime bar = iTime(_Symbol, PERIOD_M5, 0);
   if(bar == 0 || bar == g_lastBar)
      return;
   g_lastBar = bar;
   if(iTime(_Symbol, PERIOD_M5, 1) != bar - 300)
      return; // the bar ending at the decision instant is missing
   long decisionMs = ServerMsToUtcMs((long)bar * 1000);
   int minute = (int)((decisionMs / 60000) % 1440);
   bool buy = (minute == InpBuyMinuteUtc);
   bool sell = (minute == InpSellMinuteUtc);
   if(!buy && !sell)
      return;
   if(OwnPosition(ticket))
      return; // one position at a time
   int dow = DayOfWeek((datetime)(decisionMs / 1000));
   if(dow == 0 || dow == 6)
      return;
   double close = iClose(_Symbol, PERIOD_M5, 1);
   bool ok;
   if(buy)
     {
      double sl = NormalizeDouble(close - InpBuyStopPoints * _Point, _Digits);
      double tp = NormalizeDouble(close + InpBuyTargetPoints * _Point, _Digits);
      ok = g_trade.Buy(InpLots, _Symbol, 0.0, sl, tp, "AU parity");
      g_deadlineMs = InpBuyMaxHoldMin > 0 ? decisionMs + InpBuyMaxHoldMin * 60000L : 0;
     }
   else
     {
      double sl = NormalizeDouble(close + InpSellStopPoints * _Point, _Digits);
      double tp = NormalizeDouble(close - InpSellTargetPoints * _Point, _Digits);
      ok = g_trade.Sell(InpLots, _Symbol, 0.0, sl, tp, "AU parity");
      g_deadlineMs = 0;
     }
   g_flatMs = ForceFlatMs(decisionMs);
   if(!ok)
      PrintFormat("entry failed: %d %s", g_trade.ResultRetcode(), g_trade.ResultComment());
  }

void OnDeinit(const int reason)
  {
   if(!HistorySelect(0, TimeCurrent() + 86400))
      return;
   int h = FileOpen(InpOutFile, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      PrintFormat("cannot write %s: %d", InpOutFile, GetLastError());
      return;
     }
   FileWrite(h, "deal", "position", "time_msc_server", "type", "entry", "reason", "ea_reason",
             "volume", "price", "sl", "tp", "commission", "swap", "profit", "symbol");
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic)
         continue;
      long type = HistoryDealGetInteger(d, DEAL_TYPE);
      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;
      long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
      long why = HistoryDealGetInteger(d, DEAL_REASON);
      ulong pos = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
      string reason = why == DEAL_REASON_SL ? "SL" : why == DEAL_REASON_TP ? "TP" :
                      why == DEAL_REASON_EXPERT ? "EXPERT" : IntegerToString(why);
      FileWrite(h, d, pos, HistoryDealGetInteger(d, DEAL_TIME_MSC),
                type == DEAL_TYPE_BUY ? "BUY" : "SELL",
                entry == DEAL_ENTRY_IN ? "IN" : entry == DEAL_ENTRY_OUT ? "OUT" : IntegerToString(entry),
                reason, entry == DEAL_ENTRY_OUT ? ReasonFor(pos) : "",
                DoubleToString(HistoryDealGetDouble(d, DEAL_VOLUME), 2),
                DoubleToString(HistoryDealGetDouble(d, DEAL_PRICE), _Digits),
                DoubleToString(HistoryDealGetDouble(d, DEAL_SL), _Digits),
                DoubleToString(HistoryDealGetDouble(d, DEAL_TP), _Digits),
                DoubleToString(HistoryDealGetDouble(d, DEAL_COMMISSION), 2),
                DoubleToString(HistoryDealGetDouble(d, DEAL_SWAP), 2),
                DoubleToString(HistoryDealGetDouble(d, DEAL_PROFIT), 2),
                HistoryDealGetString(d, DEAL_SYMBOL));
     }
   FileClose(h);
   PrintFormat("AurumReference: %d deals written to Common\\Files\\%s", total, InpOutFile);
  }
//+------------------------------------------------------------------+
