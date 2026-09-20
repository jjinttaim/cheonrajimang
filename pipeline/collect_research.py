"""Download public, licensed research files; no execution of downloaded code."""
from pathlib import Path
import hashlib
import json
import urllib.request
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parents[1] / "data/research"
MAX_BYTES = 40_000_000


def fetch(url, target, checksum=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raw = target.read_bytes()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "SearchProof research prototype"})
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Download exceeds 40 MB limit")
        if raw[:100].lstrip().lower().startswith((b"<!doctype html", b"<html")) and target.suffix != ".html":
            raise ValueError("Expected a data file, received HTML")
        if checksum and hashlib.md5(raw).hexdigest() != checksum.split(":")[-1]:
            raise ValueError("Publisher checksum mismatch")
        target.write_bytes(raw)
    if checksum and hashlib.md5(raw).hexdigest() != checksum.split(":")[-1]:
        raise ValueError("Existing file does not match publisher checksum")
    return {"path": str(target.relative_to(OUT)), "url": url, "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "publisher_checksum": checksum}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for record, folder, role in [
        (18637221, "sar_searchers_2026", "SEARCHER_MOVEMENT"),
        (6592419, "cyprus_exercise_2022", "SEARCH_EXERCISE"),
    ]:
        endpoint = f"https://zenodo.org/api/records/{record}"
        item = fetch(endpoint, OUT/folder/"metadata.json")
        metadata = json.loads((OUT/folder/"metadata.json").read_text())
        if metadata["metadata"]["license"]["id"] != "cc-by-4.0":
            raise ValueError("Unexpected license; manual review required")
        files = [item]
        for f in metadata["files"]:
            if f["size"] > MAX_BYTES:
                raise ValueError("File too large")
            files.append(fetch(f["links"]["self"], OUT/folder/Path(f["key"]).name, f["checksum"]))
        records.append({"id": folder, "role": role, "source": f"https://zenodo.org/records/{record}",
                        "license": "CC-BY-4.0", "files": files})
        print(folder, "downloaded", flush=True)
    folder = "lost_hikers_2022"
    base = "https://media.springernature.com/original/springer-static/esm/art:10.1038%2Fs41598-022-09502-4/MediaObjects/"
    files = []
    for i in range(1, 5):
        name = f"41598_2022_9502_MOESM{i}_ESM." + ("csv" if i < 4 else "pdf")
        files.append(fetch(base+name, OUT/folder/name))
    records.append({"id": folder, "role": "INCIDENT_ENDPOINTS_AND_SEPARATE_SIMULATION",
                    "source": "https://www.nature.com/articles/s41598-022-09502-4",
                    "license": "Article CC-BY-4.0; supplementary material included unless separately credited. No separate restriction found in inspected supplement.",
                    "files": files})
    (OUT/"manifest.json").write_text(json.dumps({
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "datasets": records,
        "warning": "No fitted model. Searchers, exercise participants and lost-person endpoints must not be pooled as one target."
    }, ensure_ascii=False, indent=2))
    print("Manifest complete", flush=True)


if __name__ == "__main__":
    main()
