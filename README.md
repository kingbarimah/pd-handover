# pd-handover

Sometimes we get tired of manually typing up notes after a pagerduty shift. This script pulls the PagerDuty incidents you acknowledged, formats them into a clean list, and drops them into a daily `.txt` file. Run it as many times as you want during your shift, but be sure to SAVE the file before running it again. This will only add new incidents, leaving anything you've already written untouched.

## Output format

```
Handover:
• Env - deferredQueueAgeSeconds grrrelaymx6 (deploymentName) - [RESOLUTION]
• Env - pod-restarted kube-state-metrics (deploymentName) - [RESOLUTION]
```

Fill in the `[RESOLUTION]` fields as you go. Re-running the script won't touch them.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env                 # fill in your values
cp config.example.json config.json  # customise cleaning patterns
```

## Usage

```bash
source .env

./pd_handover.py        # last 8 hours (default)
./pd_handover.py 4      # last 4 hours
./pd_handover.py 12     # last 12 hours
```

## Configuration

The script is built to work with any PagerDuty setup. Edit `config.json` to match how your alerts are named and structured:

| Field | Description |
|---|---|
| `strip_patterns` | Regex patterns to remove from alert titles |
| `strip_prefixes` | Literal strings to remove from alert titles |
| `alert_detail_field` | The field name inside PD alert body details |
| `alert_detail_strip` | Strings to clean from the detail field |
| `line_template` | Output format when no detail is present |
| `line_template_with_detail` | Output format when detail is present |

## How deduplication works

Every time the script runs, it saves the PagerDuty incident IDs it has processed to a separate metadata JSON file. On the next run it checks that file first and skips anything already recorded. If you ever delete the handover file, the metadata resets automatically so you can regenerate it cleanly without any manual cleanup.

## Requirements

- Python 3.8+
- PagerDuty API token (read-only scope is fine)
