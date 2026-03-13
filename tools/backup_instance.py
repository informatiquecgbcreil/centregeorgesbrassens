#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in s)


def _copy_sqlite(db_uri: str, target: Path) -> None:
    src = db_uri.replace("sqlite:///", "", 1)
    src_path = Path(src)
    if not src_path.exists():
        raise RuntimeError(f"SQLite introuvable: {src_path}")
    shutil.copy2(src_path, target)


def _pg_dump(db_uri: str, target: Path) -> None:
    cmd = ["pg_dump", "--format=plain", "--no-owner", "--no-privileges", db_uri]
    with target.open("wb") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("pg_dump a échoué. Installez pg_dump/psql sur la machine.")


def _zip_dir(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if folder.exists():
            for p in folder.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(folder))


def main() -> int:
    app = create_app()
    with app.app_context():
        org = app.config.get("ORGANIZATION_NAME", "structure")
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(app.root_path).parent / "backups"
        out_dir.mkdir(parents=True, exist_ok=True)

        base = _safe_name(f"{org}_{stamp}")
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        uploads = Path(app.config.get("APP_UPLOAD_DIR"))

        db_file = out_dir / f"{base}.db"
        sql_file = out_dir / f"{base}.sql"
        uploads_zip = out_dir / f"{base}_uploads.zip"

        if db_uri.startswith("sqlite:///"):
            _copy_sqlite(db_uri, db_file)
            db_artifact = db_file
        elif db_uri.startswith("postgresql://"):
            _pg_dump(db_uri, sql_file)
            db_artifact = sql_file
        else:
            raise RuntimeError(f"Type de base non supporté automatiquement: {db_uri}")

        _zip_dir(uploads, uploads_zip)

        print("Sauvegarde créée ✅")
        print(f"- Base: {db_artifact}")
        print(f"- Uploads: {uploads_zip}")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERREUR: {e}")
        raise SystemExit(1)
