# VM Setup: Windows 11 ARM64 on VMware Fusion

Practical guide to get the MT5 side of Aurum running. Demo account only.

## 1. Overview

| Where | Responsibilities |
|---|---|
| Mac (macOS, M4) | Cursor, Git, code editing, unit tests, backtests, notebooks, strategy/risk logic. Python 3.12 via uv, venv in `.venv`. Setup: `scripts/mac_setup.sh`. |
| Windows 11 ARM64 VM | MT5 terminal (Fusion Markets), x64 Python 3.12 (emulated), `MetaTrader5` package, collector, shadow runner, demo trader. Setup: `scripts/windows_setup.ps1`. |

Only `aurum/adapters/mt5/` imports `MetaTrader5`, lazily, so everything else runs on the Mac.

## 2. VMware Fusion shared folder

1. Install VMware Tools in the VM (required for shared folders).
2. Shut down or keep the VM running, then: VM Settings -> Sharing -> enable Shared Folders.
3. Add the repo folder (on the Mac this may be `~/code/aurum` or wherever you cloned it), access **Read & Write**.
4. In the VM it appears as `\\vmware-host\Shared Folders\aurum`.
5. Map it to a drive letter (PowerShell or cmd):

   ```powershell
   net use Z: "\\vmware-host\Shared Folders" /persistent:yes
   ```

   The repo is then `Z:\aurum`.

Warnings:

- **Never put venvs or data on the share.** Use `C:\venvs\aurum` and `C:\aurum-data` (the setup script does this).
- Line endings are handled by `.gitattributes`. With the shared-folder setup, run `git` on the Mac side only.

## 3. Python x64 on Windows ARM64

The `MetaTrader5` wheels are `win_amd64` only; there is no ARM64 wheel. Windows 11 on ARM runs x64 programs under emulation, so we install **x64** Python. `scripts/windows_setup.ps1` forces this through uv with the request `cpython-3.12-windows-x86_64-none`.

Check:

```powershell
& C:\venvs\aurum\Scripts\python.exe -c "import platform; print(platform.machine())"
# AMD64  (ARM64 means the wrong interpreter was picked)
```

## 4. MT5 terminal

1. Download the MT5 installer from the Fusion Markets website / client area. The x64 installer runs under emulation.
2. Install, then log in to the **MT5 Zero demo** account.
3. Open an XAUUSD chart (check Market Watch for the broker's exact symbol name).
4. Enable algorithmic trading:
   - Toolbar "Algo Trading" button must be green.
   - Tools -> Options -> Expert Advisors -> tick "Allow algorithmic trading".
5. Tools -> Options -> Charts -> "Max bars in chart" -> **Unlimited** (needed for backfill later).
6. Keep the terminal running and logged in. The Python package attaches to a running terminal; set `MT5_PATH` in `.env` if more than one terminal is installed.

## 5. Credentials

```powershell
Copy-Item .env.example .env   # the setup script does this if .env is missing
```

Fill `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (demo). Never commit `.env`, never log these values.

## 6. Run the setup script

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Unblock-File Z:\aurum\scripts\windows_setup.ps1   # scripts on a network share may need this
Z:\aurum\scripts\windows_setup.ps1                # add -SkipTests to skip pytest
```

It installs uv and x64 Python, creates the venv at `C:\venvs\aurum`, persists `UV_PROJECT_ENVIRONMENT`, `UV_PYTHON`, `UV_LINK_MODE=copy` and `AURUM_DATA_DIR` for your user, verifies AMD64 and `import MetaTrader5`, then runs `aurum doctor`, tests and `aurum mt5 ping`. In Phase 0, `mt5 ping` is a placeholder that exits with code 2; the script treats that as a warning.

## 7. OpenSSH Server + Cursor Remote-SSH (optional)

In an elevated PowerShell in the VM:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd-private -DisplayName "OpenSSH Server (Private)" `
  -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Private
# optional: PowerShell as default shell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

If the network profile is Public, switch it to Private (Settings -> Network) rather than opening the rule to Public.

Find the VM IP with `ipconfig` (VMware NAT, typically 192.168.x.x or 172.16.x.x).

On the Mac, `~/.ssh/config`:

```
Host aurum-vm
    HostName 192.168.x.x
    User <windows-username>
    IdentityFile ~/.ssh/id_ed25519
```

Key auth: copy `~/.ssh/id_ed25519.pub` to the VM.

- Standard user: append to `C:\Users\<user>\.ssh\authorized_keys`.
- Administrator account: append to `C:\ProgramData\ssh\administrators_authorized_keys` and restrict the ACL:

  ```powershell
  icacls.exe "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
  ```

Then in Cursor: Remote-SSH -> Connect to Host -> `aurum-vm`.

**Choose one workflow and stick to it:** shared folder, Remote-SSH, or git-sync. With Remote-SSH, clone the repo inside the VM (`git clone ... C:\code\aurum`) and open that, not the shared folder. Run `scripts\windows_setup.ps1` from that clone.

## 8. Firewall and network

- MT5 needs outbound TCP 443 plus the broker's access ports; default Windows outbound rules allow this.
- Do not expose sshd on the Public profile.
- VMware NAT means the VM is not reachable from the LAN.

## 9. Clock and reliability

A drifting VM clock breaks stale-quote checks.

- Enable VMware Tools time sync with the host, and Windows time sync: `w32tm /resync`.
- Disable sleep/hibernate in the VM: `powercfg /change standby-timeout-ac 0` (and `hibernate-timeout-ac 0`). Also keep the Mac awake during sessions (e.g. `caffeinate -d` or Energy settings).
- Set Windows Update active hours to cover 06:00-21:00 London time.

## 10. Known pitfall: MT5 timestamps (to be verified in Phase 1)

MT5 Python `copy_rates_*` / `copy_ticks_*` return epoch seconds that are expressed in the **trade server's time zone** (Fusion Markets servers are commonly GMT+2 / GMT+3 with US DST), not true UTC, despite what the docs imply. Phase 1 must measure the offset (compare the latest tick time to system UTC) and convert before storing anything. Until verified, do not trust MT5 times as UTC.

## 11. Verification checklist

- [ ] `uv run aurum doctor` reports all OK
- [ ] Interpreter reports `AMD64`
- [ ] `python -c "import MetaTrader5; print(MetaTrader5.__version__)"` works in the venv
- [ ] MT5 terminal running, logged in to demo, Algo Trading green
- [ ] `uv run aurum mt5 ping` succeeds (Phase 1; exit code 2 placeholder in Phase 0)
- [ ] `uv run pytest -m mt5` passes (integration tests, Phase 1+)

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `platform.machine()` prints `ARM64`, or `MetaTrader5` fails to install | ARM64 Python picked. Delete `C:\venvs\aurum`, ensure `UV_PYTHON=cpython-3.12-windows-x86_64-none`, re-run the setup script. |
| `IPC initialize failed` | Terminal not running or not logged in, Algo Trading disabled, or wrong `MT5_PATH` when several terminals are installed. |
| Shared folder not visible | VMware Tools missing or not running; Sharing not enabled in VM Settings; `net use` mapping not made. |
| uv hardlink / "failed to link" errors | Set `UV_LINK_MODE=copy` (script does this). Make sure the venv is not on the share. |
| "running scripts is disabled" / "not digitally signed" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then `Unblock-File` the script. |
