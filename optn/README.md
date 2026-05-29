# optn

A CLI for calculating returns on options trades — short puts (`sp`) and covered calls (`cc`).

## Installation

This project uses [uv](https://docs.astral.sh/uv/) for packaging and tools.

```sh
# Install the tool globally (creates an isolated environment)
uv tool install .

# Run it
optn --help
```

Other install methods:

```sh
# Editable install (for development)
uv tool install -e .

# From a git repository
uv tool install git+https://github.com/yourname/optn.git

# Upgrade later
uv tool upgrade optn
```

## Usage

```
optn sp [options]
optn cc [options]
```

### Short Put (`sp`)

```sh
optn sp -s 100 -p 0.85
optn sp --strike 95 --premium 1.20 -o 2025-06-01
```

### Covered Call (`cc`)

```sh
optn cc -s 105 -p 0.55 -b 98.50
```

### Date formats

The `-o/--open` and `-e/--expiry` options accept flexible date inputs:

- `yyyy-mm-dd` — full date
- `mm-dd` — month/day in the current year
- `dd` — day in the current month
- `t+5` — 5 days from today
- `t-3` — 3 days ago

If omitted, `--open` defaults to today and `--expiry` defaults to the next Friday.

## Development

```sh
uv sync
uv run optn sp -s 100 -p 1.25
```

## License

MIT
