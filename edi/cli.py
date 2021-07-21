"""
cli.py

Implements a command line interface for the EDI service/application.
"""
import argparse
import asyncio

from edi.core.models import EdiResult
from edi.core.workflows import EdiProcessor
from edi.extensions.eligibilitycheck import EdiEligibilityCheckProcessor

CLI_DESCRIPTION = """
Analyze, Enrich, Validate, Translate and Transmit EDI Messages using the LinuxForHealth CLI!
The LinuxForHealth EDI CLI accepts an input EDI message and returns an EdiResult object (JSON).
The CLI's options are used to specify which EDI operations are included.
If no options are provided, the CLI will execute all available operations.
"""


def create_arg_parser() -> argparse.Namespace:
    arg_parser = argparse.ArgumentParser(
        prog="LinuxForHealth EDI",
        description=CLI_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="All messages are analyzed by default",
    )
    arg_parser.add_argument(
        "-e",
        "--enrich",
        help="supplements the EDI message with additional data",
        action="store_const",
        const="enrich",
    )
    arg_parser.add_argument(
        "-v",
        "--validate",
        help="validates the EDI message",
        action="store_const",
        const="validate",
    )
    arg_parser.add_argument(
        "-t",
        "--translate",
        help="validates the EDI message",
        action="store_const",
        const="translate",
    )
    arg_parser.add_argument(
        "-x",
        "--transmit",
        help="transmits the EDI message",
        action="store_const",
        const="transmit",
    )
    arg_parser.add_argument(
        "-p",
        "--pretty",
        help="pretty print the EDI Result",
        action="store_const",
        const="pretty",
    )

    arg_parser.add_argument("edi_file", help="the path to the EDI message")
    return arg_parser.parse_args()


async def process_edi(edi: str, **kwargs) -> EdiResult:
    """
    Processes an EDI message.
    Keyword arguments are used to drive workflow processing and align with CLI options. kwargs include:
    - enrich
    - validate
    - translate

    Additional kwargs used for processing:
    - pretty: indicates if the output EDIResult is "pretty printed"
    """
    with open(edi) as f:
        input_message = ",".join(f.readlines())

    processor: EdiProcessor = EdiEligibilityCheckProcessor(input_message)
    await processor.start_nats_coverage_eligibility_subscriber()
    processor.analyze()

    if kwargs["enrich"]:
        processor.enrich()

    if kwargs["validate"]:
        processor.validate()

    if kwargs["translate"]:
        await processor.translate()

    if kwargs["transmit"]:
        await processor.transmit()

    while not processor.message_received:
        print("Waiting for NATS message...")
        await asyncio.sleep(2)
    await processor.stop_nats_coverage_eligibility_subscriber()

    result = processor.complete()
    return result


async def run_process_edi(args: argparse.Namespace) -> None:
    """
    Runs process_edi to process the input EDI message.

    :return: None
    """
    edi_result = await process_edi(
        args.edi_file,
        enrich=args.enrich,
        validate=args.validate,
        translate=args.translate,
        transmit=args.transmit,
    )
    if args.pretty:
        print(edi_result.json(indent=4, sort_keys=True))
    else:
        print(edi_result.json())


if __name__ == "__main__":
    cli_args = create_arg_parser()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_process_edi(cli_args))

