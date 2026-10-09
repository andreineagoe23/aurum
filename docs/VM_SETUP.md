# VM Setup: Windows 11 ARM64 on VMware Fusion

Practical guide to get the MT5 side of Aurum running. Demo account only.

## 1. Overview

| Where | Responsibilities |
|---|---|
| Mac (macOS, M4) | Cursor as the editor UI, connected to the VM over Remote-SSH (host `aurum-vm`). Optionally a second clone for Mac-only work (`scripts/mac_setup.sh`, venv in `.venv`). |
| Windows 11 ARM64 VM | The working repo, MT5 terminal (Fusion Markets), x64 Python 3.12 (emulated), `MetaTrader5` package, tests, collector, shadow runner, demo trader. Setup: `scripts/windows_setup.ps1`. |

Only `aurum/adapters/mt5/` imports `MetaTrader5`, lazily, so everything else also runs on the Mac.

Actual layout inside the VM:

| What | Path |
|---|---|
| Repo (git clone; edited via Cursor Remote-SSH) | `C:\code\aurum` |
| Virtual environment (`UV_PROJECT_ENVIRONMENT`) | `C:\venvs\aurum` |
| Market data (`AURUM_DATA_DIR`) | `C:\aurum-data` |

## 2. No shared folder: clone inside the VM

VMware Fusion does **not** support shared folders for Windows 11 ARM guests, so `\\vmware-host\Shared Folders` is not available. Instead, clone the repo inside the VM and edit it from the Mac over Remote-SSH (section 7):

```powershell
git clone <repo-url> C:\code\aurum
```

Run `git` inside the VM, against `C:\code\aurum` (from the Cursor Remote-SSH terminal or a local PowerShell). Line endings are handled by `.gitattributes`.

## 3. Python x64 on Windows ARM64

The `MetaTrader5` wheels are `win_amd64` only; there is no ARM64 wheel. Windows 11 on ARM runs x64 programs under emulation, so we install **x64** Python. `scripts/windows_setup.ps1` forces this through uv with the request `cpython-3.12-windows-x86_64-none`.

Check with `sysconfig.get_platform()`, which reports the platform the interpreter was built for:

```powershell
& C:\venvs\aurum\Scripts\python.exe -c "import sysconfig; print(sysconfig.get_platform())"
# win-amd64  (win-arm64 means the wrong interpreter was picked)
```

Do **not** use `platform.machine()` for this: on Windows ARM it returns `ARM64` even for emulated x64 Python, because it reports the host CPU. `aurum doctor` and the setup script both use `sysconfig.get_platform()`.

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
Copy-Item C:\code\aurum\.env.example C:\code\aurum\.env   # the setup script does this if .env is missing
```

Fill `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` (demo). Never commit `.env`, never log these values.

## 6. Run the setup script

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
C:\code\aurum\scripts\windows_setup.ps1   # add -SkipTests to skip pytest
```

It installs uv and x64 Python, creates the venv at `C:\venvs\aurum`, persists `UV_PROJECT_ENVIRONMENT`, `UV_PYTHON`, `UV_LINK_MODE=copy` and `AURUM_DATA_DIR` (`C:\aurum-data`) for your user, verifies `win-amd64` and `import MetaTrader5`, then runs `aurum doctor`, tests and `aurum mt5 ping`. In Phase 0, `mt5 ping` is a placeholder that exits with code 2; the script treats that as a warning.

Open a new terminal (or reconnect Remote-SSH) afterwards so the persisted environment variables are picked up.

## 7. OpenSSH Server + Cursor Remote-SSH

### 7.1 sshd in the VM

In an elevated PowerShell in the VM:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd-private -DisplayName "OpenSSH Server (Private)" `
  -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Private
# PowerShell as default shell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

If the network profile is Public, switch it to Private (Settings -> Network) rather than opening the rule to Public.

Find the VM IP with `ipconfig` (VMware NAT, typically 192.168.x.x or 172.16.x.x).

### 7.2 SSH config on the Mac

`~/.ssh/config`:

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

Check from a Mac terminal: `ssh aurum-vm` should land in PowerShell without a password prompt.

### 7.3 Cursor settings

The first connection is slow (x64 emulation, server install), and Cursor's default connect timeout is too short. In Cursor's **user** settings on the Mac (`settings.json`):

```json
{
  "remote.SSH.connectTimeout": 120
}
```

Then: Remote-SSH -> Connect to Host -> `aurum-vm`, and open the folder `C:\code\aurum`.

### 7.4 Workaround: install the Cursor server manually

If the automatic server install keeps failing or timing out (the remote side downloads and unpacks the Cursor server inside the VM), install it by hand:

1. On the Mac, find Cursor's **commit id**: Cursor -> About Cursor (the "Commit" line).
2. Download the Cursor server (remote extension host) archive for that exact commit and the VM's platform, e.g. on the Mac, and copy it to the VM with `scp <archive> aurum-vm:`.
3. In the VM, remove any half-installed copy and extract the archive into the directory Cursor expects:

   ```powershell
   $Commit = '<commit-id>'
   $Dest = "$env:USERPROFILE\.cursor-server\bin\$Commit"
   Remove-Item -Recurse -Force $Dest -ErrorAction SilentlyContinue
   New-Item -ItemType Directory -Force -Path $Dest | Out-Null
   tar -xzf "$env:USERPROFILE\<archive>" -C $Dest --strip-components 1
   ```

   The server's entry files (e.g. `node.exe`, `bin\`) must sit directly under `$Dest`, not in a nested folder.
4. Reconnect from Cursor. It finds the server for its commit and skips the download.

Repeat after every Cursor update: the commit id changes, so the server directory does too. Old `bin\<commit>` directories can be deleted.

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

- [ ] `ssh aurum-vm` works with key auth; Cursor opens `C:\code\aurum` over Remote-SSH
- [ ] `uv run aurum doctor` reports all OK
- [ ] Interpreter reports `win-amd64` (`sysconfig.get_platform()`)
- [ ] `python -c "import MetaTrader5; print(MetaTrader5.__version__)"` works in the venv
- [ ] MT5 terminal running, logged in to demo, Algo Trading green
- [ ] `uv run aurum mt5 ping` succeeds (Phase 1; exit code 2 placeholder in Phase 0)
- [ ] `uv run pytest -m mt5` passes (integration tests, Phase 1+)

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `sysconfig.get_platform()` prints `win-arm64`, or `MetaTrader5` fails to install | ARM64 Python picked. Delete `C:\venvs\aurum`, ensure `UV_PYTHON=cpython-3.12-windows-x86_64-none`, re-run the setup script. |
| `platform.machine()` prints `ARM64` although the venv is x64 | Expected on Windows ARM; it reports the host CPU. Use `sysconfig.get_platform()`. |
| `IPC initialize failed` | Terminal not running or not logged in, Algo Trading disabled, or wrong `MT5_PATH` when several terminals are installed. |
| Cursor Remote-SSH times out / "Could not establish connection" | Raise `remote.SSH.connectTimeout` (7.3). If the server install itself fails, install it manually (7.4). |
| Cursor connects after an update but hangs on "Installing server" | New commit id; repeat 7.4 for the new commit. |
| uv hardlink / "failed to link" errors | Set `UV_LINK_MODE=copy` (script does this). |
| "running scripts is disabled" / "not digitally signed" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then `Unblock-File` the script if it was downloaded. |
