"""Optional command line interface for editors and scripts."""

import argparse
import shutil
import uuid
from pathlib import Path

from .audio import EXTENSIONS, MAX_BYTES, write_json
from .worker import process


def main():
    parser = argparse.ArgumentParser(description="Separate dialogue, music and sound effects from one file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    parser.add_argument("--keep", nargs="*", choices=["speech", "music", "effects"], default=["speech", "effects"])
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--no-cpu-fallback", action="store_true")
    args = parser.parse_args()
    if not args.source.is_file() or args.source.suffix.lower() not in EXTENSIONS:
        parser.error("Choose an existing supported audio or video file.")
    if args.source.stat().st_size > MAX_BYTES:
        parser.error("The file exceeds the 500 MB limit.")
    directory = args.output.resolve() / uuid.uuid4().hex
    directory.mkdir(parents=True)
    source = f"source{args.source.suffix.lower()}"
    shutil.copyfile(args.source, directory / source)
    write_json(
        directory / "request.json",
        {
            "filename": args.source.name,
            "source": source,
            "device": args.device,
            "gains": {stem: int(stem in args.keep) for stem in ("speech", "music", "effects")},
            "cpu_fallback": not args.no_cpu_fallback,
        },
    )
    print(f"Separating on {args.device}. Outputs: {directory / 'output'}", flush=True)
    process(directory)
    print(f"Complete: {directory / 'output'}")


if __name__ == "__main__":
    main()
