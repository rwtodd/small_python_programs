import argparse
from datetime import date, timedelta
from math import ceil

def handle_date_input(date_arg: str, today: date) -> date:
    """date_arg should take one of these forms...
    yyyy-mm-dd  -> full date
    mm-dd       -> from this year
    dd          -> from this month
    t+x         -> x days from today
    t-x         -> x days ago"""
    if date_arg is None:
        return today
    if date_arg.startswith('t'):
        return today + timedelta(days=int(date_arg[1:]))
    try:
        parts = [int(part) for part in date_arg.split('-')]
        match len(parts):
            case 1:
                parts = [ today.year, today.month, parts[0] ]
            case 2:
                parts = [ today.year, parts[0], parts[1] ]
            case 3:
                pass # do nothing! parts is good
            case _:
                raise ValueError("given date has too many segments!")
    except ValueError as ve:
        raise ValueError("given date isn't a valid format!") from ve
    return date(*parts)

def next_friday(today: date) -> date:
    """Calculates the date of the next Friday."""
    days_until_friday = (4 - today.weekday()) % 7  # 0=Mon, 1=Tue, ..., 6=Sun, 4=Fri
    return today + timedelta(days=days_until_friday)

def weekdays_between(start_date: date, end_date: date) -> int:
    """
    Calculates the number of weekdays between two dates (inclusive).
    """
    startnum, delta = start_date.weekday(), (end_date - start_date).days + 1
    num_saturdays = ceil((startnum+2+delta)/7) - ceil((startnum+2)/7)  # add 2 to make Saturday=5 -> 7
    num_sundays =  ceil((startnum+1+delta)/7) - ceil((startnum+1)/7)  # add 1 to make Sundays=6 -> 7
    return delta - num_saturdays - num_sundays

def run_short_put(args):
    """
    Calculates and prints the returns for a short put option.
    """
    expiry_date = args.expiry if args.expiry else next_friday(args.open)
 
    if expiry_date < args.open:
        print("Error: Expiry date cannot be before the open date!")
        return
    weekdays = weekdays_between(args.open, expiry_date)
    multiplier = (args.premium - 0.005) / args.strike + 1.0

    print(f"Days in Market: {weekdays:>13.2f}")
    print(f"Capital:       ${args.strike * 100.0:>13.2f}")
    print(f"Max Value:     ${args.premium * 100.0 - 0.5:>13.2f}")
    print(f"Pct Gain:       {multiplier - 1.0:>14.2%}")
    print(f"Pct Annualized: {((multiplier) ** (260.0 / weekdays)) - 1.0:>14.2%}")
    print(f"Break Even:    ${args.strike - args.premium:>13.2f}")


def run_covered_call(args):
    """
    Calculates and prints the returns for a covered call option.
    """
    expiry_date = args.expiry if args.expiry else next_friday(args.open)
    basis = args.basis if args.basis else args.strike

    if expiry_date < args.open:
        print("Error: Expiry date cannot be before the open date!")
        return

    weekdays = weekdays_between(args.open, expiry_date)
    max_gain = (args.strike - basis) + (args.premium - 0.005)
    multiplier = 1.0 + (max_gain / basis)
    low_gain = args.premium - 0.005
    low_mult = 1.0 + (low_gain / basis)

    print(f"Days in Market: {weekdays:>13.2f}")
    print(f"Capital:       ${basis * 100.0:>13.2f}")
    print(f"Max Value:     ${max_gain * 100.0:>13.2f}")
    print(f"Pct Max Gain:   {multiplier - 1.0:>14.2%}")
    print(f"    Annualized: {((multiplier) ** (260.0 / weekdays)) - 1.0:>14.2%}")
    print(f"Low Value:     ${low_gain * 100.0:>13.2f}")
    print(f"Pct Low Gain:   {low_mult - 1.0:>14.2%}")
    print(f"    Annualized: {((low_mult) ** (260.0 / weekdays)) - 1.0:>14.2%}")



def main():
    """
    Main function to parse command line arguments and run the appropriate option calculation.
    """
    today = date.today()
    next_fri = next_friday(today)
    parser = argparse.ArgumentParser(description="Calculate option returns.",
                                     formatter_class=argparse.RawTextHelpFormatter)  # Better help formatting
    subparsers = parser.add_subparsers(dest='command', help='Sub-command help')

    # Short Put Subparser
    sp_parser = subparsers.add_parser('sp', help='Calculate returns for a short put option')
    sp_parser.add_argument('-o', '--open', type=lambda s: handle_date_input(s, today), default=today,
                           help='Date the position was opened (defaults to today)')
    sp_parser.add_argument('-e', '--expiry', type=lambda s: handle_date_input(s, today),
                           help='Date the option expires (defaults to next Friday)')
    sp_parser.add_argument('-s', '--strike', type=float, required=True, help='Strike price of the option')
    sp_parser.add_argument('-p', '--premium', type=float, required=True, help='Premium from the sale')
    sp_parser.set_defaults(func=run_short_put)

    # Covered Call Subparser
    cc_parser = subparsers.add_parser('cc', help='Calculate returns for a covered call option')
    cc_parser.add_argument('-o', '--open',  type=lambda s: handle_date_input(s, today), default=today,
                           help='Date the position was opened (defaults to today)')
    cc_parser.add_argument('-e', '--expiry', type=lambda s: handle_date_input(s, today),
                           help='Date the option expires (defaults to next Friday)')
    cc_parser.add_argument('-s', '--strike', type=float, required=True, help='Strike price of the option')
    cc_parser.add_argument('-p', '--premium', type=float, required=True, help='Premium from the sale')
    cc_parser.add_argument('-b', '--basis', type=float,
                           help='Cost basis (defaults to the strike price)')
    cc_parser.set_defaults(func=run_covered_call)
    args = parser.parse_args()

    if args.command:  # Check if a command was provided
        args.func(args)
    else:
        parser.print_help() # If no command

if __name__ == "__main__":
    main()


