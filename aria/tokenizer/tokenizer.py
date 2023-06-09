"""Includes Tokenizers and pre-processing utilities."""

import torch
import functools
import itertools
import copy

from collections import defaultdict
from random import randint
from mido.midifiles.units import second2tick, tick2second

from aria.data.midi import MidiDict
from aria.config import load_config

# TODO:
# - Write tests


class Tokenizer:
    """Abstract Tokenizer class for tokenizing midi_dict objects.

    Args:
        padding (bool): If True, add padding to tokenized sequences.
        truncate_type (str): Style in which to truncate the tokenized sequences.
            Options are "none", "default", and "strided".
        max_seq_len (int): Sequence length to truncate according to.
        stride_len (int, optional): Stride length to use if truncate_type is
            "strided".
        return_tensors (bool, optional): If True, encode will return tensors.
            Defaults to False.
    """

    def __init__(
        self,
        padding: bool,
        truncate_type: str,
        max_seq_len: int,
        stride_len: int = None,
        return_tensors: bool = False,
    ):
        assert truncate_type in {
            "none",
            "default",
            "strided",
        }, "Invalid value for truncation_type"

        if truncate_type == "strided":
            assert stride_len is not None and (
                0 < stride_len < max_seq_len
            ), "stride_len must be between 0 and max_seq_len"

        self.padding = padding
        self.max_seq_len = max_seq_len
        self.truncate_type = truncate_type
        self.stride_len = stride_len
        self.return_tensors = return_tensors

        # These must be implemented in child class (abstract params)
        self.tok_to_id = {}
        self.id_to_tok = {}
        self.vocab_size = -1

        self.bos_tok = "<S>"
        self.eos_tok = "<E>"
        self.pad_tok = "<P>"
        self.unk_tok = "<U>"

    def tokenize_midi_dict(self, midi_dict: MidiDict):
        """Abstract method for tokenizing a MidiDict object into a sequence of
        tokens."""
        raise NotImplementedError

    def detokenize_midi_dict(self, tokenized_seq: list):
        """Abstract method for de-tokenizing a sequence of tokens into a
        MidiDict Object."""
        raise NotImplementedError

    def encode(self, unencoded_seq: list):
        """Converts tokenized sequence into a list/torch.Tensor of ids."""

        def _enc_fn(tok):
            return self.tok_to_id.get(tok, self.tok_to_id[self.unk_tok])

        if self.tok_to_id is None:
            raise NotImplementedError("tok_to_id")

        if self.return_tensors is True:
            encoded_seq = torch.tensor([_enc_fn(tok) for tok in unencoded_seq])
        else:
            encoded_seq = [_enc_fn(tok) for tok in unencoded_seq]

        return encoded_seq

    def decode(self, encoded_seq: list | torch.Tensor):
        """Converts sequence of ids into the corresponding list of tokens."""

        def _dec_fn(id):
            return self.id_to_tok.get(id, self.unk_tok)

        if self.id_to_tok is None:
            raise NotImplementedError("id_to_tok")

        if isinstance(encoded_seq, torch.Tensor):
            decoded_seq = [_dec_fn(idx) for idx in encoded_seq.tolist()]
        else:
            decoded_seq = [_dec_fn(idx) for idx in encoded_seq]

        return decoded_seq


class TokenizerLazy(Tokenizer):
    """Lazy MidiDict Tokenizer"""

    def __init__(
        self,
        padding: bool,
        truncate_type: str,
        max_seq_len: int,
        stride_len: int = None,
        return_tensors: bool = False,
    ):
        super().__init__(
            padding, truncate_type, max_seq_len, stride_len, return_tensors
        )
        self.config = load_config()["tokenizer"]["lazy"]

        # Calculate time quantizations (in ms)
        self.num_time_step = self.config["time_quantization"]["num_steps"]
        self.min_time_step = self.config["time_quantization"]["min_step"]
        self.time_step_quantizations = [
            self.min_time_step * i for i in range(self.num_time_step)
        ]
        self.max_time_step = self.time_step_quantizations[-1]

        # Calculate velocity quantizations
        self.velocity_step = self.config["velocity_quantization"]["step"]
        self.velocity_quantizations = [
            i * self.velocity_step
            for i in range(int(127 / self.velocity_step) + 1)
        ]
        self.max_velocity = self.velocity_quantizations[-1]

        self.instrument_tokens = [
            k
            for k, v in self.config["ignore_instruments"].items()
            if v is False
        ] + ["drums"]

        # Build vocab
        self.special_tokens = [
            self.bos_tok,
            self.eos_tok,
            self.pad_tok,
            self.unk_tok,
        ]

        self.wait_tokens = [("wait", i) for i in self.time_step_quantizations]
        self.drum_tokens = [("drum", i) for i in range(35, 82)]

        self.note_tokens = itertools.product(
            self.instrument_tokens,
            [i for i in range(128)],
            self.velocity_quantizations,
        )
        self.note_tokens = list(self.note_tokens)

        self.vocab = (
            self.special_tokens
            + self.instrument_tokens
            + self.note_tokens
            + self.drum_tokens
            + self.wait_tokens
        )

        self.tok_to_id = {tok: idx for idx, tok in enumerate(self.vocab)}
        self.id_to_tok = {v: k for k, v in self.tok_to_id.items()}
        self.vocab_size = len(self.vocab)
        self.pad_id = self.tok_to_id[self.pad_tok]

        self.program_to_instrument = (
            {i: "piano" for i in range(0, 7 + 1)}
            | {i: "chromatic" for i in range(8, 15 + 1)}
            | {i: "organ" for i in range(16, 23 + 1)}
            | {i: "guitar" for i in range(24, 31 + 1)}
            | {i: "bass" for i in range(32, 39 + 1)}
            | {i: "strings" for i in range(40, 47 + 1)}
            | {i: "ensemble" for i in range(48, 55 + 1)}
            | {i: "brass" for i in range(56, 63 + 1)}
            | {i: "reed" for i in range(64, 71 + 1)}
            | {i: "pipe" for i in range(72, 79 + 1)}
            | {i: "synth_lead" for i in range(80, 87 + 1)}
            | {i: "synth_pad" for i in range(88, 95 + 1)}
            | {i: "synth_effect" for i in range(96, 103 + 1)}
            | {i: "ethnic" for i in range(104, 111 + 1)}
            | {i: "percussive" for i in range(112, 119 + 1)}
            | {i: "sfx" for i in range(120, 127 + 1)}
        )

    def _remove_instruments(self, midi_dict: MidiDict):
        """Removes all messages with instruments specified in config, excluding
        drums."""
        instruments_to_remove = self.config["ignore_instruments"]
        midi_dict = copy.deepcopy(midi_dict)

        programs_to_remove = [
            i
            for i in range(1, 127 + 1)
            if instruments_to_remove[self.program_to_instrument[i]] is True
        ]
        channels_to_remove = [
            msg["channel"]
            for msg in midi_dict.instrument_msgs
            if msg["data"] in programs_to_remove
        ]

        # Remove drums (channel 9/16) from channels to remove
        channels_to_remove = [i for i in channels_to_remove if i not in {9, 16}]

        # Remove unwanted messages all type by looping over msgs types
        for msgs_name, msgs_list in midi_dict._get_msg_dict().items():
            setattr(
                midi_dict,
                msgs_name,
                [
                    msg
                    for msg in msgs_list
                    if msg.get("channel", -1) not in channels_to_remove
                ],
            )

        return midi_dict

    def _build_pedal_intervals(self, midi_dict: MidiDict):
        """Returns pedal-on intervals for each channel."""
        channel_to_pedal_intervals = defaultdict(list)
        pedal_status = {}

        for pedal_msg in midi_dict.pedal_msgs:
            tick = pedal_msg["tick"]
            channel = pedal_msg["channel"]
            data = pedal_msg["data"]

            if data == 1 and pedal_status.get(channel, None) is None:
                pedal_status[channel] = tick
            elif data == 0 and pedal_status.get(channel, None) is not None:
                # Close pedal interval
                _start_tick = pedal_status[channel]
                _end_tick = tick
                channel_to_pedal_intervals[channel].append(
                    [_start_tick, _end_tick]
                )
                del pedal_status[channel]

        # Close all unclosed pedals at end of track
        final_tick = midi_dict.note_msgs[-1]["data"]["end"]
        for channel, start_tick in pedal_status.items():
            channel_to_pedal_intervals[channel].append([start_tick, final_tick])

        return channel_to_pedal_intervals

    @classmethod
    def _find_closest_int(cls, n: int, sorted_list: list):
        # Selects closest integer to n from sorted_list
        # Time ~ Log(n)

        left, right = 0, len(sorted_list) - 1
        closest = float("inf")

        while left <= right:
            mid = (left + right) // 2
            diff = abs(sorted_list[mid] - n)

            if diff < abs(closest - n):
                closest = sorted_list[mid]

            if sorted_list[mid] < n:
                left = mid + 1
            else:
                right = mid - 1

        return closest

    def _quantize_time(self, time: int):
        # This function will return values res >= 0 (inc. 0)
        return TokenizerLazy._find_closest_int(
            time, self.time_step_quantizations
        )

    def _quantize_velocity(self, velocity: int):
        # This function will return values in the range 0 < res =< 127
        velocity_quantized = TokenizerLazy._find_closest_int(
            velocity, self.velocity_quantizations
        )

        if velocity_quantized == 0 and velocity != 0:
            return self.velocity_step
        else:
            return velocity_quantized

    def tokenize_midi_dict(self, midi_dict: MidiDict):
        ticks_per_beat = midi_dict.ticks_per_beat
        midi_dict = self._remove_instruments(midi_dict)
        channel_to_pedal_intervals = self._build_pedal_intervals(midi_dict)

        channel_to_instrument = {
            msg["channel"]: self.program_to_instrument[msg["data"]]
            for msg in midi_dict.instrument_msgs
            if msg["channel"] not in {9, 16}  # Exclude drums
        }

        # Add non-drums to present_instruments (prefix)
        present_instruments = [
            self.program_to_instrument[msg["data"]]
            for msg in midi_dict.instrument_msgs
            if msg["channel"] not in {9, 16}
        ]

        # Add drums to present_instruments (prefix) if channels 9/16 are used
        channels_used = {msg["channel"] for msg in midi_dict.instrument_msgs}
        if 9 in channels_used or 16 in channels_used:
            present_instruments.append("drums")

        present_instruments = list(set(present_instruments))

        # NOTE: Any preceding silence is removed implicitly
        tokenized_seq = []
        num_notes = len(midi_dict.note_msgs)
        for i, msg in enumerate(midi_dict.note_msgs):
            # Special case instrument is a drum. This occurs exclusively when
            # MIDI channel is 9, 16 when 0 indexing
            if msg["channel"] in {9, 16}:
                _pitch = msg["data"]["pitch"]
                tokenized_seq.append(("drum", _pitch))

            else:  # Non drum case (i.e. an instrument note)
                _instrument = channel_to_instrument[msg["channel"]]
                _pitch = msg["data"]["pitch"]
                _velocity = msg["data"]["velocity"]
                _start_tick = msg["data"]["start"]
                _end_tick = msg["data"]["end"]

                # Update _end_tick if affected by pedal
                for pedal_interval in channel_to_pedal_intervals[
                    msg["channel"]
                ]:
                    pedal_start, pedal_end = (
                        pedal_interval[0],
                        pedal_interval[1],
                    )
                    if (
                        pedal_start <= _start_tick < pedal_end
                        and _end_tick < pedal_end
                    ):
                        _end_tick = pedal_end

                _note_duration = _get_duration_ms(
                    start_tick=_start_tick,
                    end_tick=_end_tick,
                    tempo_msgs=midi_dict.tempo_msgs,
                    ticks_per_beat=ticks_per_beat,
                )

                # Quantize
                _velocity = self._quantize_velocity(_velocity)
                _note_duration = self._quantize_time(_note_duration)
                if _note_duration == 0:
                    _note_duration = self.min_time_step

                tokenized_seq.append((_instrument, _pitch, _velocity))
                tokenized_seq.append(("dur", _note_duration))

            # Only add wait token if there is a msg after the current one
            if i <= num_notes - 2:
                _wait_duration = _get_duration_ms(
                    start_tick=msg["data"]["start"],
                    end_tick=midi_dict.note_msgs[i + 1]["data"]["start"],
                    tempo_msgs=midi_dict.tempo_msgs,
                    ticks_per_beat=ticks_per_beat,
                )

                # If wait duration is longer than maximum quantized time step
                # append max_time_step tokens repeatedly
                while _wait_duration > self.max_time_step:
                    tokenized_seq.append(("wait", self.max_time_step))
                    _wait_duration -= self.max_time_step

                # Only append wait tok if it is non-zero
                _wait_duration = self._quantize_time(_wait_duration)
                if _wait_duration != 0:
                    tokenized_seq.append(("wait", _wait_duration))

        # Return according to truncation setting
        if self.truncate_type == "none":
            _res = (
                present_instruments
                + [self.bos_tok]
                + tokenized_seq
                + [self.eos_tok]
            )
            res = [_res]
        elif self.truncate_type == "default":
            _res = (
                present_instruments
                + [self.bos_tok]
                + tokenized_seq
                + [self.eos_tok]
            )
            if self.padding is True:
                _res += [self.pad_tok] * (self.max_seq_len - len(_res))
            res = [_res[: self.max_seq_len]]
        elif self.truncate_type == "strided":
            _res = [self.bos_tok] + tokenized_seq + [self.eos_tok]
            seq_len = len(_res)
            prefix_len = len(present_instruments)

            res = []
            idx = 0
            # No padding needed here
            while idx + self.max_seq_len - prefix_len < seq_len:
                res.append(
                    present_instruments
                    + _res[idx : idx + self.max_seq_len - prefix_len]
                )
                idx += self.stride_len

            # Add the last sequence
            _seq = (
                present_instruments
                + _res[idx : idx + self.max_seq_len - prefix_len]
            )
            if self.padding is True:
                _seq += [self.pad_tok] * (self.max_seq_len - len(_seq))

            res.append(_seq)

        return res

    # TODO:
    # - There is a minor bug with repeated notes occurring whilst the pedal is
    #  down. It sounds like the note is turned off and on again when the second
    #  note plays.
    def detokenize_midi_dict(self, tokenized_seq: list):
        instrument_programs = self.config["instrument_programs"]
        instrument_names = instrument_programs.keys()
        ticks_per_beat = 480
        tempo = 500000

        # Set messages
        tempo_msgs = [{"type": "tempo", "data": tempo, "tick": 0}]
        meta_msgs = []
        pedal_msgs = []

        # Drum instrument messages
        instrument_msgs = [
            {
                "type": "instrument",
                "data": 1,
                "tick": 0,
                "channel": 9,
            }
        ]
        instrument_to_channel = {"drum": 9}

        # Add non-drum instrument_msgs, breaks at first note token
        for idx, tok in enumerate(tokenized_seq):
            if tok in instrument_names:
                instrument_msgs.append(
                    {
                        "type": "instrument",
                        "data": instrument_programs[tok],
                        "tick": 0,
                        "channel": idx,
                    }
                )
                assert tok not in instrument_to_channel.keys(), "Dupe"
                instrument_to_channel[tok] = idx

            elif tok in self.special_tokens or tok == "drums":
                continue
            else:
                start = idx
                break

        # Note messages
        note_msgs = []
        curr_tick = 0
        for curr_tok, next_tok in zip(
            tokenized_seq[start:], tokenized_seq[start + 1 :]
        ):
            if curr_tok in self.special_tokens:
                _tok_type = "special"
            else:
                _tok_type = curr_tok[0]

            if _tok_type == "dur" or _tok_type == "special":
                continue
            elif _tok_type == "wait":
                curr_tick += int(
                    second2tick(
                        second=1e-3 * curr_tok[1],
                        ticks_per_beat=ticks_per_beat,
                        tempo=tempo,
                    )
                )
            elif _tok_type == "drum":
                _tick = curr_tick
                _pitch = curr_tok[1]
                _channel = instrument_to_channel["drum"]
                _velocity = self.config["drum_velocity"]
                _start_tick = curr_tick
                _end_tick = curr_tick + self.min_time_step

                note_msgs.append(
                    {
                        "type": "note",
                        "data": {
                            "pitch": _pitch,
                            "start": _start_tick,
                            "end": _end_tick,
                            "velocity": _velocity,
                        },
                        "tick": _tick,
                        "channel": _channel,
                    }
                )

            else:  # Case curr_tok, next_tok are note, dur respectively
                duration = next_tok[1]

                _tick = curr_tick
                _channel = instrument_to_channel[curr_tok[0]]
                _pitch = curr_tok[1]
                _velocity = curr_tok[2]
                _start_tick = curr_tick
                _end_tick = curr_tick + int(
                    second2tick(
                        second=1e-3 * duration,
                        ticks_per_beat=ticks_per_beat,
                        tempo=tempo,
                    )
                )

                note_msgs.append(
                    {
                        "type": "note",
                        "data": {
                            "pitch": _pitch,
                            "start": _start_tick,
                            "end": _end_tick,
                            "velocity": _velocity,
                        },
                        "tick": _tick,
                        "channel": _channel,
                    }
                )

        return MidiDict(
            meta_msgs=meta_msgs,
            tempo_msgs=tempo_msgs,
            pedal_msgs=pedal_msgs,
            instrument_msgs=instrument_msgs,
            note_msgs=note_msgs,
            ticks_per_beat=ticks_per_beat,
        )

    def export_pitch_aug(self, aug_range: int):
        """Exports a function that augments the pitch of all note tokens.

        Note that notes which fall out of the range (0, 127) will be replaced
        with the unknown token '<U>'.

        Args:
            aug_range (int): Returned function will randomly augment the pitch
                from a value in the range (-aug_range, aug_range).

        Returns:
            Callable[list]: Exported function.
        """

        def pitch_aug_seq(
            src: list,
            unk_tok: str,
            _aug_range: float,
        ):
            def pitch_aug_tok(tok, _pitch_aug):
                if isinstance(tok, str):
                    _tok_type = "special"
                else:
                    _tok_type = tok[0]

                if (
                    _tok_type == "special"
                    or _tok_type == "dur"
                    or _tok_type == "drum"
                    or _tok_type == "wait"
                ):
                    # Return without changing
                    return tok
                else:
                    # Return augmented tok
                    (_instrument, _pitch, _velocity) = tok

                    if 0 <= _pitch + _pitch_aug <= 127:
                        return (_instrument, _pitch + _pitch_aug, _velocity)
                    else:
                        return unk_tok

            pitch_aug = randint(-_aug_range, _aug_range)
            return [pitch_aug_tok(x, pitch_aug) for x in src]

        # See functools.partial docs
        return functools.partial(
            pitch_aug_seq,
            unk_tok=self.unk_tok,
            _aug_range=aug_range,
        )

    def export_velocity_aug(self, aug_steps_range: int):
        """Exports a function which augments the velocity of all pitch tokens.

        This augmentation truncated such that it returns a valid note token.

        Args:
            aug_steps_range (int): Returned function will randomly augment
                velocity in the range aug_steps_range * (-self.velocity_step,
                self.velocity step).

        Returns:
            Callable[str]: Exported function.
        """

        def velocity_aug_seq(
            src: list,
            velocity_step: int,
            max_velocity: int,
            _aug_steps_range: float,
        ):
            def velocity_aug_tok(tok, _velocity_aug):
                if isinstance(tok, str):
                    _tok_type = "special"
                else:
                    _tok_type = tok[0]

                if (
                    _tok_type == "special"
                    or _tok_type == "dur"
                    or _tok_type == "drum"
                    or _tok_type == "wait"
                ):
                    # Return without changing
                    return tok
                else:
                    # Return augmented tok
                    (_instrument, _pitch, _velocity) = tok

                    # Check it doesn't go out of bounds
                    if _velocity + _velocity_aug >= max_velocity:
                        return (_instrument, _pitch, max_velocity)
                    elif _velocity + _velocity_aug <= velocity_step:
                        return (_instrument, _pitch, velocity_step)

                    return (_instrument, _pitch, _velocity + _velocity_aug)

            velocity_aug = velocity_step * randint(
                -_aug_steps_range, _aug_steps_range
            )
            return [velocity_aug_tok(x, velocity_aug) for x in src]

        # See functools.partial docs
        return functools.partial(
            velocity_aug_seq,
            velocity_step=self.velocity_step,
            max_velocity=self.max_velocity,
            _aug_steps_range=aug_steps_range,
        )

    # TODO: Implement - follow export_pitch aug
    def export_time_aug(self):
        # Remember special case where we have max_time_step
        raise NotImplementedError


def _get_duration_ms(
    start_tick: int,
    end_tick: int,
    tempo_msgs: list,
    ticks_per_beat: int,
):
    """Calculates elapsed time between start_tick and end_tick (in ms)"""

    # Finds idx such that:
    # tempo_msg[idx]["tick"] < start_tick <= tempo_msg[idx+1]["tick"]
    for idx, curr_msg in enumerate(tempo_msgs):
        if start_tick <= curr_msg["tick"]:
            break
    if idx > 0:  # Special case idx == 0 -> Don't -1
        idx -= 1

    # It is important that we initialise curr_tick & curr_tempo here. In the
    # case that there is a single tempo message the following loop will not run.
    duration = 0.0
    curr_tick = start_tick
    curr_tempo = tempo_msgs[idx]["data"]

    # Sums all tempo intervals before tempo_msgs[-1]["tick"]
    for curr_msg, next_msg in zip(tempo_msgs[idx:], tempo_msgs[idx + 1 :]):
        curr_tempo = curr_msg["data"]
        if end_tick < next_msg["tick"]:
            delta_tick = end_tick - curr_tick
        else:
            delta_tick = next_msg["tick"] - curr_tick

        duration += tick2second(
            tick=delta_tick,
            tempo=curr_tempo,
            ticks_per_beat=ticks_per_beat,
        )

        if end_tick < next_msg["tick"]:
            break
        else:
            curr_tick = next_msg["tick"]

    # Case end_tick > tempo_msgs[-1]["tick"]
    if end_tick > tempo_msgs[-1]["tick"]:
        curr_tempo = tempo_msgs[-1]["data"]
        delta_tick = end_tick - curr_tick

        duration += tick2second(
            tick=delta_tick,
            tempo=curr_tempo,
            ticks_per_beat=ticks_per_beat,
        )

    # Convert from seconds to milliseconds
    duration = duration * 1e3
    duration = round(duration)

    return duration
