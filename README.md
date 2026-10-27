# Cycling Trainer

A simple indoor training app for Windows: structured workouts, ERG mode and live stats, without video graphics. It controls smart trainers such as the Elite Suito, Direto, Justo and Avanti over Bluetooth (FTMS) and reads a Bluetooth heart rate strap.

## Download

Get the latest installer, `CyclingTrainer-Setup-<version>.exe`, from the [Releases page](https://github.com/ateboekhorst/cycling-trainer/releases/latest). It runs on 64-bit Windows 10 and 11 and doesn't need Python. You need Bluetooth LE (built in, or a USB Bluetooth 4.0+ adapter) and a smart trainer with Bluetooth FTMS.

The installer isn't code-signed, so Windows SmartScreen shows "Windows protected your PC" the first time. Click **More info → Run anyway**.

## Setup from source

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Run

```powershell
.venv\Scripts\python -m trainer      # or double-click run.bat
```

1. Click **Connect trainer**, pedal to wake the trainer, and pick it from the list. Close Zwift, TrainerRoad and the Elite app first, because a trainer accepts only one Bluetooth connection. Choose **Simulator** to try the app without hardware.
2. Click **Connect HR** to pair a heart rate strap (optional).
3. Set your **FTP**, choose a workout and start pedaling. The ride starts automatically.

| Key | Action |
|---|---|
| Space | Start / pause / resume |
| Up / Down | Change workout intensity ±1% (ERG), or change the slope ±0.5% (free ride / ERG off) |

When you click **Finish**, the ride is saved to `Documents\CyclingTrainer\rides` as a `.fit` file (upload it to Strava, Garmin Connect or intervals.icu) and a `.csv` file.

## FTP test

Choose **Ramp FTP Test** from the workout list. After a 5-minute warm-up, the ERG target rises by 6% of your current FTP every minute. Ride until you can't hold the target, then stop pedaling. The test ends automatically, and the summary suggests a new FTP: 75% of your best 1-minute power. Click **Use … W as my FTP** to accept it. The trainer releases the resistance as soon as the test ends; pick **Free Ride** to cool down.

## Training plan (Claude)

The **Plan** tab shows a calendar with a 4-week plan aimed at raising your FTP. Claude (`claude-opus-5-5`) writes both the schedule and the individual workouts.

1. Choose how the app reaches Claude:
   - **Claude subscription (no setup):** if no API key is set, the app uses Claude Code, which counts against your Claude Pro or Max usage limits. It uses the newest Claude Code bundled with the VS Code extension, or `claude` on your PATH. Claude Code must be logged in. Each update takes about 2 minutes.
   - **API key:** click **API key…** and paste an Anthropic API key from platform.claude.com. It's stored in Windows Credential Manager. An `ANTHROPIC_API_KEY` environment variable also works. When a key is set, it takes precedence over Claude Code. Each update is a paid API call.
2. Set **Rides per week** and **Ride duration**. Optionally add notes for the coach, such as "no rides on Wednesday".
3. Click **Create plan with Claude**. Generating a plan takes about a minute.

Claude receives your FTP, the date of your last FTP test, your rides from the last 6 weeks (planned vs. actual TSS, missed sessions) and your current Fitness (CTL), Fatigue (ATL) and Form (TSB). From these it plans progressive, TSS-based training, with lighter weeks and FTP tests where needed. After every saved ride, the plan is updated automatically in the background, based on the TSS you actually rode. Click **Ride ▶** on a day to load that workout into the Ride tab.

The plan is stored in `Documents\CyclingTrainer\plan.json`, and each ride has a small `.json` summary next to its `.fit` file.

## Workouts

The library has 54 built-in workouts, grouped by the **power zone** (Coggan, % of FTP) of each workout's main set: 10 each in zones 1–5, plus anaerobic and sprint sessions.

| Zone | Name | Power | Examples |
|---|---|---|---|
| 1 | Recovery | up to 55% FTP | Recovery 45, Flush Ride 35, Shakeout 30 |
| 2 | Endurance | 56–75% | Endurance Pyramids, Rolling Hills Endurance, Long Steady 120 |
| 3 | Tempo / sweet spot | 76–90% | Tempo 2x20, Sweet Spot 2x20, Sweet Spot Progression |
| 4 | Threshold | 91–105% | Threshold 3x10, Over-Unders 4x6, Threshold Ladder |
| 5 | VO2max | 106–120% | VO2max 4x4, 30/15 Intervals, VO2max Builds |
| 6 | Anaerobic | 121–150% | 30/30 Intervals, 15/15 Micro-Bursts, Anaerobic 8x1 |
| 7 | Sprint | above 150% | Sprint Intervals 8x20s |

Set **LTHR** (lactate-threshold heart rate) next to FTP to see your heart-rate zone on the heart-rate tile and the time in each heart-rate zone in the ride summary. Workouts are always targeted on power.

Filter by zone with the list next to the workout selector. The training plan mainly uses these workouts, lengthened with zone 2 riding to match your ride duration. Claude writes its own workout only when nothing in the library fits. The workouts are defined in `trainer/library.py`.

You can also import your own workouts. To add your own, click **Import…** and pick Zwift `.zwo`, `.mrc` (percent of FTP) or `.erg` (watts) files. They are copied to `Documents\CyclingTrainer\workouts`.

## Installer

`dist\installer\CyclingTrainer-Setup-<version>.exe` installs the app on any 64-bit Windows 10 or 11 PC without Python. It installs for the current user without admin rights, with an option to install for all users, adds a Start-menu entry and an optional desktop shortcut, and includes an uninstaller. Uninstalling keeps rides, workouts, the plan and settings.

To build it:

```powershell
.\build_installer.ps1
```

The script runs the tests, bundles the app with PyInstaller (`packaging\cycling_trainer.spec`) and wraps it with Inno Setup 6 (`packaging\installer.iss`). The version comes from `trainer\__init__.py`.

Requirements on the target PC: Bluetooth LE (built in, or a USB Bluetooth 4.0+ adapter) and a smart trainer with Bluetooth FTMS. For the training plan, each user needs their own Anthropic API key or Claude Code. The installer isn't code-signed, so Windows SmartScreen shows "Windows protected your PC" the first time: click **More info → Run anyway**.

## Development

```powershell
.venv\Scripts\python -m pytest
```

Settings and the log file are in `%APPDATA%\CyclingTrainer`.

| Module | Purpose |
|---|---|
| `trainer/devices/` | Bluetooth: FTMS trainer, heart rate strap, simulator; `DeviceManager` runs BLE on its own asyncio thread |
| `trainer/workouts.py` | Workout model, built-ins, .zwo/.mrc/.erg import |
| `trainer/session.py` | Sensor buffering and the ride state machine (auto start/pause, 1 Hz samples) |
| `trainer/metrics.py` | NP, IF, TSS, kJ, power zones |
| `trainer/export.py` | .fit, .csv and ride-log export |
| `trainer/history.py`, `trainer/fitness.py` | Ride log; CTL / ATL / TSB |
| `trainer/coach.py`, `trainer/plan.py` | Claude prompt, structured output and validation; the stored plan |
| `trainer/ui/` | PySide6 main window, chart, dialogs, theme |
