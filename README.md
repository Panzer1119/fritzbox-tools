# Fritz!Box Log Agent

Stream Fritz!Box logs in jsonl or text format. Supports a one-shot run and an agent mode that polls and avoids duplicates.

## Install

```bash
python -m pip install -e .
```

## Usage

One-shot (prints current log entries):

```bash
fritz-log-agent --base-url http://fritz.box --username alice --output-format jsonl
```

Agent mode (poll every 60s and dedupe):

```bash
fritz-log-agent --agent --interval 60 --output-format jsonl
```

Write output to a file:

```bash
fritz-log-agent --agent --output-format text --output /tmp/fritz.log
```

Print the raw payload once (one-shot only):

```bash
fritz-log-agent --print-payload --output /tmp/fritz-payload.json
```

Credentials can be passed via arguments or environment variables:

```bash
export FRITZBOX_USERNAME=alice
export FRITZBOX_PASSWORD=secret
fritz-log-agent --agent
```

## Options

- `--base-url`: Fritz!Box base URL (default: `http://fritz.box`).
- `--username`: Fritz!Box username (prompted if missing).
- `--password`: Fritz!Box password (prompted if missing).
- `--output-format` `jsonl|text`: output format for entries.
- `--output` `PATH|-|stdout`: output destination (append mode for entries).
- `--print-payload`: print the full JSON response once (one-shot only).
- `--agent`: run continuously.
- `--interval`: polling interval in seconds.
- `--timeout`: HTTP timeout in seconds.
- `--debug`: enable debug logging.
