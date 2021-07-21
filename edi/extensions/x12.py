"""
x12.py

A single file temporary representation of Dixon Whitmire's X12SegmentReader from LinuxForHealth/X12.
"""
import os
from functools import lru_cache
from io import TextIOBase, StringIO
from os.path import dirname, abspath
from pydantic import BaseSettings, Field
from typing import Union, Iterator, List


class X12Config(BaseSettings):
    """
    X12 Parsing and Validation Configurations
    """

    isa_element_separator: int = 3
    isa_repetition_separator: int = 82
    isa_segment_length: int = 106
    isa_segment_terminator: int = 105

    #x12_character_set: str = Field(regex="^(BASIC|EXTENDED)$")
    x12_reader_buffer_size: int = 1024000

    class Config:
        case_sensitive = False
        env_file = os.path.join(dirname(dirname(abspath(__file__))), ".env")

@lru_cache
def get_config():
    """Returns the X12Config"""
    return X12Config()


class X12SegmentReader:
    """
    Streams segments from a X12 message or file.
    with X12Reader(x12_data) as r:
       for segment in r.segments():
          # do something interesting
    Segments are streamed in order using a buffered generator function.
    The buffer size is configured using the config/env variable X12_READER_BUFFER_SIZE which defaults to 1MB.
    """

    def __init__(self, x12_input: str):
        """
        Initializes the X12SegmentReader with a x12 input.
        The x12 input may be a message payload or a path to a x12 file.
        :param x12_input: The X12 Message or a path to a X12 file
        """

        self.x12_input: str = x12_input

        # cache config settings
        config: X12Config = get_config()
        self.buffer_size: int = config.x12_reader_buffer_size
        self.isa_segment_length: int = config.isa_segment_length
        self.isa_element_separator: int = config.isa_element_separator
        self.isa_repetition_separator: int = config.isa_repetition_separator
        self.isa_segment_terminator: int = config.isa_segment_terminator

        # set in __enter__
        self.x12_stream: Union[None, TextIOBase] = None
        self.element_separator: Union[None, str] = None
        self.repetition_separator: Union[None, str] = None
        self.segment_terminator: Union[None, str] = None

    def _set_delimiters(self):
        """
        Sets the X12 message delimiters based on leading ISA segment/control header.
        The ISA segment is conveyed in the first 106 characters of the transmission.
        """
        self.x12_stream.seek(0)

        isa_segment: str = self.x12_stream.read(self.isa_segment_length)
        self.element_separator = isa_segment[self.isa_element_separator]
        self.repetition_separator = isa_segment[self.isa_repetition_separator]
        self.segment_terminator = isa_segment[self.isa_segment_terminator]

    def __enter__(self) -> "X12SegmentReader":
        """
        Initializes the X12 Stream and parses message delimiters
        :return: The X12SegmentReader instance
        :raise: ValueError if the x12 input is invalid
        """
        if is_x12_file(self.x12_input):
            self.x12_stream = open(self.x12_input, "r")
        elif is_x12_data(self.x12_input):
            self.x12_stream = StringIO(self.x12_input)
        else:
            raise ValueError(
                "Invalid x12_input. Expecting X12 Message or valid path to X12 File"
            )

        self.x12_stream.seek(0)

        if not self.x12_stream.read(self.isa_segment_length):
            raise ValueError("Invalid X12Stream")

        self._set_delimiters()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Closes the X12SegmentReader's X12 Stream
        :param exc_type: Exception Type
        :param exc_val: Exception Value
        :param exc_tb: Exception traceback
        """
        if not self.x12_stream.closed:
            self.x12_stream.close()

    def segments(self) -> Iterator[List[str]]:
        """
        Iterator function used to return X12 segments from the underlying X12 stream.
        The read buffer size may be configured using X12_READER_BUFFER_SIZE.
        :return: X12 Segment
        """
        self.x12_stream.seek(0)
        while True:
            buffer: str = self.x12_stream.read(self.buffer_size)

            if not buffer:
                break

            while buffer[-1] != self.segment_terminator:
                next_character: str = self.x12_stream.read(1)
                if not next_character:
                    break
                buffer += next_character

            # use rstrip to remove trailing empty strings
            for segment in buffer.rstrip(self.segment_terminator).split(
                self.segment_terminator
            ):
                yield segment

    def elements(self, segment: str, delimiter: int) -> List[str]:
        """
        Return X12 elements of a segment.

        :return: X12 segment elements
        """
        return segment.split(delimiter)


def is_x12_data(input_data: str) -> bool:
    """
    Returns True if the input data appears to be a X12 message.
    :param input_data: Input data to evaluate
    :return: True if the input data is a x12 message, otherwise False
    """
    return input_data.startswith("ISA") if input_data else False


def is_x12_file(file_path: str) -> bool:
    """
    Returns true if the file path exists and is a x12 file.
    Environment and user variables are expanded within the file path.
    :param file_path: The file path to test.
    :return: True if the file path is a x12 file, otherwise false
    """
    if not file_path:
        return False

    expanded_path = os.path.expandvars(os.path.expanduser(file_path))
    if not os.path.exists(expanded_path) or os.path.isdir(expanded_path):
        return False

    with (open(expanded_path, "r")) as f:
        f.seek(0)
        # ISA segment is first 106 characters
        config: X12Config = get_config()
        isa_segment = f.read(config.isa_segment_length)
        return is_x12_data(isa_segment)
