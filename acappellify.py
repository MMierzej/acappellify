# -*- coding: utf-8 -*-
"""acappellify.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1GgI0LQ2oYAwE7DeWTNtlKxIg2mtfp6RM

Each section is comprehensively explained in the Chapter 3: _Methodology_ of the thesis.

Bear in mind that installing dependencies and downloading models may take 5 minutes or more.

# Musical source separation (Demucs)
"""

!python3 -m pip install -qU git+https://github.com/facebookresearch/demucs#egg=demucs
!pip install -q torchvision==0.15.2

# heavily sourced from https://colab.research.google.com/drive/1dC9nVxk3V_VPjUADsnFu8EiT-xnU1tGH?usp=sharing

import io
from pathlib import Path
import select
from shutil import rmtree
import subprocess as sp
import sys
from typing import Dict, Tuple, Optional, IO

from google.colab import files


class Demucs:
    def __init__(self, model: str = "htdemucs_ft") -> None:
        self.model = model

    def separate(self, inp: Path, outp: Path) -> Path:
        cmd = ["python3", "-m", "demucs.separate", "-o", str(outp), "-n", self.model, str(inp)]
        print("Executing command: ", " ".join(cmd))
        p = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE)
        self.__copy_process_streams(p)
        p.wait()
        if p.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)}")
        return Path(outp) / self.model / inp.stem

    def from_upload(self) -> None:
        out_path = Path('separated')
        in_path = Path('tmp_in')

        if in_path.exists():
            rmtree(in_path)
        in_path.mkdir()

        if out_path.exists():
            rmtree(out_path)
        out_path.mkdir()

        uploaded = files.upload()
        for name, content in uploaded.items():
            (in_path / name).write_bytes(content)
        self.separate(in_path, out_path)

    def __copy_process_streams(self, process: sp.Popen) -> None:
        def raw(stream: Optional[IO[bytes]]) -> IO[bytes]:
            assert stream is not None
            if isinstance(stream, io.BufferedIOBase):
                stream = stream.raw
            return stream

        p_stdout, p_stderr = raw(process.stdout), raw(process.stderr)
        stream_by_fd: Dict[int, Tuple[IO[bytes], io.StringIO, IO[str]]] = {
            p_stdout.fileno(): (p_stdout, sys.stdout),
            p_stderr.fileno(): (p_stderr, sys.stderr),
        }
        fds = list(stream_by_fd.keys())

        while fds:
            # `select` syscall will wait until one of the file descriptors has content.
            ready, _, _ = select.select(fds, [], [])
            for fd in ready:
                p_stream, std = stream_by_fd[fd]
                raw_buf = p_stream.read(2 ** 16)
                if not raw_buf:
                    fds.remove(fd)
                    continue
                buf = raw_buf.decode()
                std.write(buf)
                std.flush()

"""# Transcription to MIDI (Basic Pitch)"""

!pip install -q librosa pretty_midi basic-pitch[onnx]  # onnx to run on CPU to avoid conflicts with the CUDA libs downgraded by demucs

from pathlib import Path
from typing import Union

import basic_pitch as bp
import basic_pitch.inference
from basic_pitch.inference import Model as BasicPitchModel
import librosa
from pretty_midi import PrettyMIDI


def _get_bp_model_path(model_type: BasicPitchModel.MODEL_TYPES) -> Path:
    filename_suffix = None
    match model_type:
        case BasicPitchModel.MODEL_TYPES.ONNX:
            filename_suffix = bp.FilenameSuffix.onnx
        case BasicPitchModel.MODEL_TYPES.TENSORFLOW:
            filename_suffix = bp.FilenameSuffix.tf
        case BasicPitchModel.MODEL_TYPES.TFLITE:
            filename_suffix = bp.FilenameSuffix.tflite
        case _:
            filename_suffix = bp.FilenameSuffix.onnx
    return bp.build_icassp_2022_model_path(filename_suffix)


class BasicPitch:
    def __init__(
        self,
        model_type: BasicPitchModel.MODEL_TYPES = BasicPitchModel.MODEL_TYPES.ONNX,
    ) -> None:
        self.model = bp.inference.Model(_get_bp_model_path(model_type))

    def get_midi(
        self,
        audio_path: Union[Path, str],
        midi_bpm: float = 120.0,
    ) -> PrettyMIDI:
        _, midi, _ = bp.inference.predict(audio_path,
                                          model_or_model_path=self.model,
                                          onset_threshold=0.7,
                                          frame_threshold=0.35,
                                          midi_tempo=midi_bpm)
        return midi

"""# MIDI processing"""

!pip install -q pretty_midi librosa

from collections import defaultdict
from itertools import groupby
import re

import librosa
from pretty_midi import Instrument, Note, PrettyMIDI


def midi_from_notes(track: list[Note]) -> PrettyMIDI:
    midi = PrettyMIDI()
    instrument = Instrument(program=0)
    midi.instruments.append(instrument)
    instrument.notes = track
    return midi

def extract_octave(note: Note) -> int:
    symbol = librosa.midi_to_note(note.pitch, unicode=False)
    octave = re.search(r"\d+$", symbol)
    if octave is not None:
        return int(octave.group())
    else:
        raise ValueError(f"Couldn't extract octave from symbol: {symbol}")

def split_into_octaves(midi: PrettyMIDI) -> dict[int, PrettyMIDI]:
    midi_notes = [note for note in midi.instruments[0].notes if note.pitch >= librosa.note_to_midi("A0")]

    notes_by_octave = defaultdict(list)
    for octave, notes in groupby(midi_notes, extract_octave):
        notes_by_octave[octave].extend(notes)

    return {octave: midi_from_notes(notes) for octave, notes in notes_by_octave.items()}

def transpose_by_semitones(midi: PrettyMIDI, semitones: int) -> PrettyMIDI:
    transposed_notes = []
    for note in midi.instruments[0].notes:
        transposed_note = Note(note.velocity, note.pitch + semitones, note.start, note.end)
        transposed_notes.append(transposed_note)
    return midi_from_notes(transposed_notes)

def to_octave(midi: PrettyMIDI, octave: int) -> PrettyMIDI:
    def adjust_octave(note: Note) -> Note:
        current_octave = extract_octave(note)
        semitones_diff = 12 * (octave - current_octave)
        return Note(note.velocity, note.pitch + semitones_diff, note.start, note.end)

    return midi_from_notes(list(map(adjust_octave, midi.instruments[0].notes)))

def constrain_pitch_range(midi: PrettyMIDI, min_octave: int, max_octave: int) -> PrettyMIDI:
    filtered_notes = []
    for note in midi.instruments[0].notes:
        if min_octave <= extract_octave(note) <= max_octave:
            filtered_notes.append(note)
    return midi_from_notes(filtered_notes)

def to_many_monophonic(poly_midi: PrettyMIDI) -> list[PrettyMIDI]:
    mono_tracks = []

    for note in sorted(poly_midi.instruments[0].notes, key=lambda note: note.start):
        track = next((track for track in mono_tracks if track[-1].end <= note.start), [])
        if len(track) == 0:
            mono_tracks.append(track)
        track.append(note)

    return list(map(midi_from_notes, mono_tracks))

"""# Vocal synthesis (DiffSinger)"""

!git clone --quiet --single-branch https://github.com/MMierzej/diffsinger.git
!pip install -q -r diffsinger/requirements-colab-inference-e2e.txt
!pip install -q pretty_midi pydub
!cp -r diffsinger/configs .  # workaround
!mkdir -p usr
!cp -r diffsinger/usr/configs usr/  # workaround
!mkdir checkpoints

# pretrained DiffSinger model
!curl -L -O \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://github.com/MoonInTheRiver/DiffSinger/releases/download/pretrain-model/0228_opencpop_ds100_rel.zip
!unzip -q 0228_opencpop_ds100_rel.zip model_ckpt_steps_160000.ckpt config.yaml -d checkpoints/0228_opencpop_ds100_rel

# pitch estimator
!curl -L -O \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://github.com/MoonInTheRiver/DiffSinger/releases/download/pretrain-model/0102_xiaoma_pe.zip
!unzip -q 0102_xiaoma_pe.zip -d checkpoints/0102_xiaoma_pe

# vocoder (mel-spectrogram -> waveform)
#   config
!curl -L -O \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://github.com/MoonInTheRiver/DiffSinger/releases/download/pretrain-model/0109_hifigan_bigpopcs_hop128.zip
!unzip -q 0109_hifigan_bigpopcs_hop128.zip config.yaml -d checkpoints/0109_hifigan_bigpopcs_hop128
#   model
!curl -L --output checkpoints/0109_hifigan_bigpopcs_hop128/model_ckpt_steps_1512000.ckpt \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://github.com/MoonInTheRiver/DiffSinger/releases/download/pretrain-model/model_ckpt_steps_1512000.ckpt

from datetime import datetime
import math
import re
import tempfile
from typing import Optional, Tuple

from pretty_midi import PrettyMIDI
from pydub import AudioSegment

from diffsinger.inference.svs.ds_e2e import DiffSingerE2EInfer
from diffsinger.utils.audio import save_wav
from diffsinger.utils.hparams import set_hparams


class DiffSinger:
    SILENCE_MIN_DURATION_S = 0.4
    _MIDI_PITCH_BREATH = -1
    _MIDI_PITCH_SILENCE = -2

    def __init__(
        self,
        config_path: str = "usr/configs/midi/e2e/opencpop/ds100_adj_rel.yaml",
        experiment_name: str = "0228_opencpop_ds100_rel",
    ):
        hparams = set_hparams(
            config=config_path,
            exp_name=experiment_name,
            print_hparams=False,
        )
        self.model = DiffSingerE2EInfer(hparams)
        self.sample_rate = hparams["audio_sample_rate"]

    def vocalize(self, mono_midi: PrettyMIDI) -> AudioSegment:
        ds_batches_and_offsets = self._mono_midi_to_ds_batches(mono_midi)
        ds_batches = [ds_batch for ds_batch, _ in ds_batches_and_offsets]
        offsets = [offset for _, offset in ds_batches_and_offsets]

        wavs = [self.model.infer_once(ds_batch) for ds_batch in ds_batches]

        vocal_segments = []
        for wav in wavs:
            with tempfile.NamedTemporaryFile() as tmp_f:
                save_wav(wav, tmp_f.name, self.sample_rate)
                vocal_segment = AudioSegment.from_wav(tmp_f.name)
                vocal_segments.append(vocal_segment)

        midi_end_s = mono_midi.instruments[0].notes[-1].end
        durations_ms = [round((next - current) * 1000)
                        for current, next in zip([0.0] + offsets,
                                                 offsets + [midi_end_s],
                                                 strict=True)]

        vocal = AudioSegment.silent(durations_ms[0], self.sample_rate)
        for vocal_segment, duration_ms in zip(vocal_segments, durations_ms[1:], strict=True):
            adjusted_vocal_segment = None
            if len(vocal_segment) > duration_ms:
                adjusted_vocal_segment = vocal_segment.fade(
                    to_gain=-120.0, end=duration_ms, duration=self.SILENCE_MIN_DURATION_S  # every segment's input should end with a silence
                )[:duration_ms]
            else:
                padding = AudioSegment.silent(duration_ms - len(vocal_segment), self.sample_rate)
                adjusted_vocal_segment = vocal_segment + padding
            vocal += adjusted_vocal_segment

        return vocal

    def _mono_midi_to_ds_batches(
        self,
        mono_midi: PrettyMIDI,
        single_octave: Optional[int] = None,
    ) -> list[tuple[dict[str, str], float]]:
        # TODO: cap batch length too

        ds_notes = self._mono_midi_to_ds_notes(mono_midi, single_octave)

        batches = []
        offset = 0.0
        current_batch = []
        current_batch_offset = 0.0
        for symbol, duration, phonemes in ds_notes + [("rest", self.SILENCE_MIN_DURATION_S, ["SP"])]:
            offset += duration
            current_batch.append((symbol, duration, phonemes))
            if phonemes == ["SP"]:
                batches.append((current_batch, current_batch_offset))
                current_batch = []
                current_batch_offset = offset

        return [(self._ds_notes_to_dict(batch), offset) for batch, offset in batches]

    def _mono_midi_to_ds_notes(
        self,
        mono_midi: PrettyMIDI,
        single_octave: Optional[int] = None,
    ) -> list[tuple[str, float, list[str]]]:
        def midi_pitch_to_note_symbol(pitch: int) -> str:
            symbol = librosa.midi_to_note(pitch, unicode=False) if pitch >= 0 else "rest"
            if single_octave is not None:
                symbol = re.sub(r"\d+", str(single_octave), symbol)
            return symbol

        def midi_pitch_to_phonemes(pitch: int) -> list[str]:
            match pitch:
                case pitch if pitch >= 0:
                    return ["n", "a"]
                case self._MIDI_PITCH_BREATH:
                    return ["AP"]
                case self._MIDI_PITCH_SILENCE:
                    return ["SP"]
                case _:
                    raise ValueError(f"Unknown pitch: {pitch}")

        def construct_note(pitch: int, duration: float) -> tuple[str, float, list[str]]:
            symbol = midi_pitch_to_note_symbol(pitch)
            phonemes = midi_pitch_to_phonemes(pitch)
            return (symbol, duration, phonemes)

        notes = []  # list[tuple[symbol, duration, list[phoneme]]]
        midi_notes = mono_midi.instruments[0].notes
        midi_start_offset = midi_notes[0].start
        if midi_start_offset != 0:
            # insert silence at the beginning
            notes.append(construct_note(self._MIDI_PITCH_SILENCE, midi_start_offset))
        for current_note, next_note in zip(midi_notes, midi_notes[1:]):
            gap = next_note.start - current_note.end
            if gap < self.SILENCE_MIN_DURATION_S:
                # lengthen current note
                notes.append(construct_note(current_note.pitch, next_note.start - current_note.start))
            else:
                # split into note and rest
                notes.append(construct_note(current_note.pitch, current_note.end - current_note.start))
                notes.append(construct_note(self._MIDI_PITCH_SILENCE, gap))
        last_midi_note = midi_notes[-1]
        notes.append(construct_note(last_midi_note.pitch, last_midi_note.end - last_midi_note.start))

        return notes

    def _ds_notes_to_dict(self, ds_notes: list[tuple[str, float, list[str]]]) -> dict[str, str]:
        notes = [(symbol, duration, phoneme) for symbol, duration, phonemes in ds_notes for phoneme in phonemes]
        return {
            "input_type": "phoneme",
            "text": "",  # doesn't matter, but is required
            "ph_seq": " ".join(phoneme for _, _, phoneme in notes),
            "note_seq": " ".join(symbol for symbol, _, _ in notes),
            "note_dur_seq": " ".join(str(duration) for _, duration, _ in notes),
            "is_slur_seq": " ".join("0" * len(notes)),
        }

"""# Singing voice conversion (Fish Diffusion)"""

# Commented out IPython magic to ensure Python compatibility.
!git clone https://github.com/fishaudio/fish-diffusion.git fishdiffusion
# %cd fishdiffusion
!git checkout 8b21f57080e70675aaaa2ffa2fad04aed9119420
!sed -i 's/from fish_audio_preprocess.utils import loudness_norm, separate_audio/from fish_audio_preprocess.utils import loudness_norm/' fish_diffusion/utils/audio.py
# %cd ..
!cp -r fishdiffusion/configs/_base_/ configs/
!mkdir -p configs/
!mkdir -p checkpoints/
!curl -s -L --output configs/M4Singer.py https://huggingface.co/spaces/fishaudio/fish-diffusion/resolve/main/configs/M4Singer.py?download=true
!curl -s -L --output checkpoints/M4Singer.ckpt https://huggingface.co/spaces/fishaudio/fish-diffusion/resolve/main/checkpoints/M4Singer.ckpt?download=true

!pip install -q mmengine==0.4.0 fish-audio-preprocess==0.2.8 torchcrepe pyworld

# Commented out IPython magic to ensure Python compatibility.
from mmengine import Config

# %cd fishdiffusion
from tools.hifisinger.inference import HiFiSingerSVCInference
# %cd ..


def get_speaker_for_octave(octave: int) -> str:
    if octave >= 5:
        return "M4Singer-Soprano-1"
    elif octave >= 4:
        return "M4Singer-Alto-1"
    elif octave >= 3:
        return "M4Singer-Tenor-1"
    else:
        return "M4Singer-Bass-1"

"""# Mixing"""

!sudo apt -qq install -y ffmpeg

from pathlib import Path
from typing import Union


# TODO: extract the Acappellifier::_ffmpeg_mix method into here?


def normalize_audio(
    audio_path: Union[str, Path],
    output_path: Union[str, Path],
    lufs: int = -14,
    lra: int = 7,
    peak_db: int = -1,
) -> None:
    ffmpeg_norm_cmd = [
        "ffmpeg -y",
        f"-i {audio_path}",
        "-filter:a",
        f"\"loudnorm=I={lufs}:LRA={lra}:TP={peak_db}\"",
        str(output_path),
    ]
    subprocess.run(" ".join(ffmpeg_norm_cmd), shell=True, check=True)

"""# Acappellifier"""

!pip install -q pydub pretty_midi

from collections import defaultdict
from itertools import chain
from pathlib import Path
import pprint
import subprocess
from tempfile import NamedTemporaryFile
from typing import Union

from pretty_midi import PrettyMIDI
from pydub import AudioSegment


DIFFSINGER_FRIENDLY_OCTAVE = 4


class Acappellifier:
    def __init__(
        self,
        demucs: Demucs,
        basic_pitch: BasicPitch,
        diff_singer: DiffSinger,
        hifi_singer_svc: HiFiSingerSVCInference,
    ) -> None:
        self.demucs = demucs
        self.basic_pitch = basic_pitch
        self.diff_singer = diff_singer
        self.hifi_singer_svc = hifi_singer_svc

    def acappellify(self, song_path: Union[str, Path]) -> Path:
        song_path = Path(song_path)

        acappella_segment_paths = []
        for segment in self._slice_input(AudioSegment.from_file(song_path)):
            with NamedTemporaryFile(delete=False, suffix=".wav") as tmpf:
                segment.export(tmpf.name, format="wav")
                acappella_segment_path = self._acappellify_single(Path(tmpf.name))
                acappella_segment_paths.append(acappella_segment_path)

        if len(acappella_segment_paths) == 0:
            raise RuntimeError(f"No acappella segments produced for '{song_path}'")

        print(f"Acapella segment paths: {acappella_segment_paths}")

        # concatenation of output fragments
        acappella_segments = list(map(AudioSegment.from_file, acappella_segment_paths))
        acappella = acappella_segments[0]
        for segment in acappella_segments[1:]:
            acappella = acappella.append(segment, crossfade=1000)

        acappella_path = Path("acappellas") / f"{song_path.stem}_acappella.wav"
        acappella_path.parent.mkdir(parents=True, exist_ok=True)
        acappella.export(acappella_path, format="wav")
        return acappella_path

    def _acappellify_single(self, song_path: Path) -> Path:
        stems_dir = self._separate(Path(song_path), Path("separated"))
        stems = ["other", "bass"]

        midi_by_stem = self._get_midi_for_stems(stems, stems_dir)
        midi_by_octave_by_stem = {stem: split_into_octaves(midi) for stem, midi in midi_by_stem.items()}
        mono_midis_by_octave_by_stem = {stem: {octave: to_many_monophonic(midi) for octave, midi in midi_by_octave.items()}
                                        for stem, midi_by_octave in midi_by_octave_by_stem.items()}

        vocal_paths_by_octave = self._vocalize_midis(mono_midis_by_octave_by_stem)

        song_vocals_path = stems_dir / "vocals.wav"
        return self._mix(song_vocals_path, vocal_paths_by_octave)

    def _vocalize_midis(
        self,
        mono_midis_by_octave_by_stem: dict[str, dict[int, list[PrettyMIDI]]]
    ) -> dict[int, list[Path]]:
        output_dir = Path("diffsinger_output") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        vocal_paths_by_octave = defaultdict(list)
        for stem, mono_midis_by_octave in mono_midis_by_octave_by_stem.items():
            for octave, mono_midis in mono_midis_by_octave.items():
                for i, mono_midi in enumerate(mono_midis):
                    vocal_path = self._vocalize_mono_midi(mono_midi, octave, i, output_dir / stem)
                    vocal_paths_by_octave[octave].append(vocal_path)

        return vocal_paths_by_octave

    def _vocalize_mono_midi(self, mono_midi: PrettyMIDI, octave: int, i: int, output_dir: Path) -> Path:
        semitones_diff = 12 * (DIFFSINGER_FRIENDLY_OCTAVE - octave)
        transposed_midi = transpose_by_semitones(mono_midi, semitones_diff)

        vocal_segment = self.diff_singer.vocalize(transposed_midi)

        output_dir.mkdir(parents=True, exist_ok=True)
        vocal_path = output_dir / f"octave{octave}_mono{i}.wav"
        vocal_segment.export(vocal_path, format="wav")

        transposed_vocal_path = self._transpose_vocal(vocal_path, DIFFSINGER_FRIENDLY_OCTAVE, octave)
        return transposed_vocal_path

    def _transpose_vocal(self, vocal_path: Path, current_octave: int, target_octave: int) -> Path:
        semitones_diff = 12 * (target_octave - current_octave)

        stem_suffix = f"transposed{'+' if semitones_diff >= 0 else '-'}{abs(semitones_diff)}"
        transposed_vocal_path = vocal_path.parent / f"{vocal_path.stem}_{stem_suffix}.wav"

        try:
            self.hifi_singer_svc.inference(
                input_path=str(vocal_path),
                output_path=str(transposed_vocal_path),
                speaker=get_speaker_for_octave(target_octave),
                pitch_adjust=semitones_diff,
                extract_vocals=False,
            )
        except Exception as e:
            print(e)
            print(f"Returning unmodified vocal '{vocal_path}'")
            return vocal_path
        else:
            return transposed_vocal_path

    def _separate(self, song_path: Path, output_dir: Path) -> Path:
        stems_dir = output_dir / self.demucs.model / song_path.stem
        if not stems_dir.exists():
            return self.demucs.separate(song_path, output_dir)
        return stems_dir

    def _get_midi_for_stems(self, stems: list[str], stems_dir: Path) -> dict[str, PrettyMIDI]:
        stem_midis = {}
        for stem in stems:
            stem_path = (stems_dir / stem).with_suffix(".wav")
            stem_midis[stem] = self._get_midi_for_stem(stem, stem_path)
        return stem_midis

    def _get_midi_for_stem(self, stem: str, stem_path: Path) -> PrettyMIDI:
        min_octave, max_octave = 1, 6
        match stem:
            case "other":
                min_octave, max_octave = 3, 6
            case "bass":
                min_octave, max_octave = 1, 2
        midi = self.basic_pitch.get_midi(stem_path)
        return constrain_pitch_range(midi, min_octave, max_octave)

    def _mix(
        self,
        song_vocals_path: Path,
        vocal_paths_by_octave: dict[int, list[Path]]
    ) -> Path:
        def get_volume_adjustment_db(octave: int) -> int:
            if octave >= 3:
                return -3
            elif octave >= 2:
                return -6
            else:
                return -9

        output_dir = Path("mixes") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "mix.wav"

        # song_vocals_norm_path = output_dir / "vocals_norm.wav"
        # normalize_audio(song_vocals_path, song_vocals_norm_path)

        audio_paths_and_input_adjustments_db = [
            (path, get_volume_adjustment_db(octave))
            for octave, paths in vocal_paths_by_octave.items()
            for path in paths
        ] + [(song_vocals_path, 0)]  # TODO: adjusting the volume reduction

        self._ffmpeg_mix(audio_paths_and_input_adjustments_db, output_path)
        return output_path

    def _ffmpeg_mix(
        self,
        audio_paths_and_input_adjustments_db: list[tuple[Path, int]],
        output_path: Path,
    ) -> None:
        audio_paths = [path for path, _ in audio_paths_and_input_adjustments_db]
        adjustments_db = [reduction for _, reduction in audio_paths_and_input_adjustments_db]

        input_args = " ".join(f"-i {path.absolute()}" for path in audio_paths)
        volume_filters = ";".join(f"[{i}:a]volume={reduction}dB[a{i}]" for i, reduction in enumerate(adjustments_db))
        amix_inputs = "".join(f"[a{i}]" for i in range(len(audio_paths)))

        filter_complex = f"\"{volume_filters};{amix_inputs}amix=inputs={len(audio_paths)}:duration=longest:dropout_transition=2\""

        with NamedTemporaryFile(delete=False, suffix=".wav") as tmpf:
            ffmpeg_mix_cmd = [
                "ffmpeg -y",
                input_args,
                "-filter_complex",
                filter_complex,
                "-ac 2",
                "-ar 44100",
                "-f wav",
                tmpf.name,
            ]
            subprocess.run(" ".join(ffmpeg_mix_cmd), shell=True, check=True)
            normalize_audio(tmpf.name, output_path)

    def _slice_input(self, audio: AudioSegment) -> list[AudioSegment]:
        def get_segment_end(
            potential_end_ms: int,
            total_length_ms: int,
            last_segment_min_length_ms: int
        ) -> int:
            end = min(potential_end_ms, total_length_ms)
            if total_length_ms - end < last_segment_min_length_ms:
                end = total_length_ms
            return end

        total_length_ms = len(audio)
        first_segment_length_ms = int(10.5 * 1000)
        subsequent_segment_length_ms = 11 * 1000
        last_segment_min_length_ms = 4 * 1000
        overlap_ms = 1000

        segments = []

        end = get_segment_end(first_segment_length_ms, total_length_ms, last_segment_min_length_ms)
        segments.append(audio[:end])
        last_end = end
        while last_end < total_length_ms:
            start = max(0, last_end - overlap_ms)
            end = get_segment_end(start + subsequent_segment_length_ms, total_length_ms, last_segment_min_length_ms)
            segments.append(audio[start:end])
            last_end = end

        return segments

"""# Experiments"""

from pathlib import Path
from typing import Union

from google.colab import files


def upload_file() -> Union[Path, None]:
    uploaded = files.upload()
    if uploaded:
        return list(uploaded.keys())[0]
    else:
        return None

import torch

device = "cuda" if torch.cuda.is_available() else "cpu"

demucs = Demucs()
basic_pitch = BasicPitch()
diff_singer = DiffSinger()
hifi_singer_svc = HiFiSingerSVCInference(
    Config.fromfile("configs/M4Singer.py"),
    "checkpoints/M4Singer.ckpt"
).to(device)

acappellifier = Acappellifier(demucs, basic_pitch, diff_singer, hifi_singer_svc)

song_path = upload_file()  # or just a path if the file already exists

# original song
AudioSegment.from_file(song_path)

# Commented out IPython magic to ensure Python compatibility.
# %%time
# assert song_path is not None, "Please, upload a song first"
# # NOTE: first run always takes longer due to lazy imports taking place and models being downloaded
# arrangement_path = acappellifier.acappellify(song_path)
# AudioSegment.from_file(arrangement_path)

files.download(arrangement_path)