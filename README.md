# Fritz!Box Log Agent

Stream Fritz!Box logs to stdout in jsonl or text format. Supports a one-shot run and an agent mode that polls and avoids duplicates.

## Install

```bash
python -m pip install -e .
```

## Usage

One-shot (prints current log entries):

```bash
fritz-log-agent --base-url http://fritz.box --username alice --stdout-format jsonl
```

Agent mode (poll every 60s and dedupe):

```bash
fritz-log-agent --agent --interval 60 --stdout-format jsonl
```

Credentials can be passed via arguments or environment variables:

```bash
export FRITZBOX_USERNAME=alice
export FRITZBOX_PASSWORD=secret
fritz-log-agent --agent
```

## Options

- `--stdout-format` `jsonl|text`: output format to stdout.
- `--agent`: run continuously.
- `--interval`: polling interval in seconds.
- `--state-file`: path to a small state file used for de-duplication.
