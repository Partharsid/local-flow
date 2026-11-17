"""Turn the contributed samples into a training manifest.

    python export_dataset.py --data ./data --out dataset.jsonl [--mode HINGLISH] [--min-sec 1] [--max-sec 30]

One JSON line per sample: {"audio": "<path>", "seconds": 4.2, "mode": "HINGLISH", "raw_text": "...", "final_text": "..."}.
`raw_text` is what the speech model heard, `final_text` is what the app typed after cleanup. Neither is a
human-verified transcript: review or re-label a sample before treating it as ground truth (a good first pass is
to transcribe with a bigger model, e.g. Whisper-Hindi2Hinglish-Apex, and keep clips where it agrees with `raw_text`).

Installs that used "Delete my data" are already gone from the database and the disk, so they never appear here.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./data")
    ap.add_argument("--out", default="dataset.jsonl")
    ap.add_argument("--mode", default="")
    ap.add_argument("--min-sec", type=float, default=0.8)
    ap.add_argument("--max-sec", type=float, default=30.0)
    a = ap.parse_args()
    data = Path(a.data)
    db = sqlite3.connect(str(data / "meta.db"))
    n = skipped = 0
    with open(a.out, "w", encoding="utf-8") as out:
        for sid, path, seconds, meta in db.execute("SELECT id, path, seconds, meta FROM samples ORDER BY created"):
            m = json.loads(meta or "{}")
            audio = data / path
            if not audio.is_file() or not (a.min_sec <= seconds <= a.max_sec) or (a.mode and m.get("mode") != a.mode):
                skipped += 1
                continue
            out.write(json.dumps({"id": sid, "audio": str(audio), "seconds": round(seconds, 2), "mode": m.get("mode"),
                                  "raw_text": m.get("raw_text", ""), "final_text": m.get("final_text", "")},
                                 ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} samples to {a.out} ({skipped} skipped)")


if __name__ == "__main__":
    main()
