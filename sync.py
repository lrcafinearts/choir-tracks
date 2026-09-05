#!/usr/bin/env python3
"""
Mirrors the choir Drive folders into this repository.

Runs on a schedule in GitHub Actions. Walks each folder listed in
folders.json, downloads anything new or changed into files/, removes
anything that's been deleted from Drive, and writes manifest.json
describing the whole tree for the website to read.

Files are stored under their Drive ID rather than their name, so that
renames, duplicate names and awkward characters can't break anything.
The display name lives in the manifest.
"""

import json
import os
import pathlib
import sys
import time

import requests

API = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

ROOT = pathlib.Path(__file__).parent
FILES_DIR = ROOT / "files"
MANIFEST = ROOT / "manifest.json"

API_KEY = os.environ.get("DRIVE_API_KEY", "").strip()
if not API_KEY:
    sys.exit("DRIVE_API_KEY is not set. Add it as a repository secret.")

session = requests.Session()

EXTENSIONS = {
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


def kind_of(mime):
    if mime.startswith("audio"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image"):
        return "image"
    return "other"


def extension_for(name, mime):
    suffix = pathlib.Path(name).suffix.lower()
    if suffix and len(suffix) <= 5:
        return suffix
    return EXTENSIONS.get(mime, "")


def get(url, **kwargs):
    """Drive occasionally rate limits. Back off and try again."""
    for attempt in range(5):
        response = session.get(url, timeout=120, **kwargs)
        if response.status_code < 400:
            return response
        if response.status_code in (403, 429, 500, 502, 503) and attempt < 4:
            time.sleep(2 ** attempt)
            continue
        raise SystemExit(f"Drive returned {response.status_code} for {url}")
    raise SystemExit("Gave up talking to Drive")


def list_folder(folder_id):
    items, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken, files(id,name,mimeType,md5Checksum,size)",
            "orderBy": "name",
            "pageSize": 1000,
            "key": API_KEY,
        }
        if token:
            params["pageToken"] = token
        data = get(API, params=params).json()
        items.extend(data.get("files", []))
        token = data.get("nextPageToken")
        if not token:
            return items


def download(file_id, destination):
    response = get(f"{API}/{file_id}", params={"alt": "media", "key": API_KEY}, stream=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "wb") as handle:
        for chunk in response.iter_content(chunk_size=1 << 16):
            handle.write(chunk)


def walk(folder_id, previous, keep, depth=0):
    """Returns the children of one Drive folder, downloading as it goes."""
    if depth > 8:
        return []

    children = []
    for item in list_folder(folder_id):
        mime = item.get("mimeType", "")

        if mime == FOLDER_MIME:
            children.append({
                "type": "folder",
                "name": item["name"],
                "children": walk(item["id"], previous, keep, depth + 1),
            })
            continue

        kind = kind_of(mime)
        if kind == "other":
            continue

        file_id = item["id"]
        path = f"files/{file_id}{extension_for(item['name'], mime)}"
        checksum = item.get("md5Checksum", "")
        keep.add(path)

        on_disk = (ROOT / path).exists()
        unchanged = previous.get(file_id) == checksum and checksum

        if not on_disk or not unchanged:
            print(f"  downloading {item['name']}")
            download(file_id, ROOT / path)

        children.append({
            "type": "file",
            "name": item["name"],
            "path": path,
            "kind": kind,
            "id": file_id,
            "md5": checksum,
        })

    return children


def collect_checksums(node, into):
    for child in node:
        if child["type"] == "folder":
            collect_checksums(child["children"], into)
        else:
            into[child["id"]] = child.get("md5", "")
    return into


def main():
    folders = json.loads((ROOT / "folders.json").read_text())

    previous = {}
    if MANIFEST.exists():
        try:
            old = json.loads(MANIFEST.read_text())
            for choir in old.get("choirs", []):
                collect_checksums(choir.get("children", []), previous)
        except (ValueError, KeyError):
            pass

    keep = set()
    choirs = []

    for entry in folders:
        if entry == "divider":
            choirs.append("divider")
            continue

        print(f"{entry['name']}")
        choirs.append({
            "name": entry["name"],
            "children": walk(entry["folderId"], previous, keep),
        })

    manifest = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "choirs": choirs,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    removed = 0
    if FILES_DIR.exists():
        for path in FILES_DIR.iterdir():
            if f"files/{path.name}" not in keep:
                path.unlink()
                removed += 1

    print(f"\n{len(keep)} files tracked, {removed} removed")


if __name__ == "__main__":
    main()
