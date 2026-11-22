"""Encrypted FamilyGuard database backup and restore CLI.

Run from backend/: python backup_restore.py backup /secure/path/file.fgbk
Run from backend/: python backup_restore.py restore /secure/path/file.fgbk --confirm-overwrite
"""
import argparse
from pathlib import Path

try:
    import main as familyguard
except ImportError:
    from . import main as familyguard


def run():
    parser=argparse.ArgumentParser(description="FamilyGuard encrypted database backup and restore")
    sub=parser.add_subparsers(dest="command",required=True)
    backup=sub.add_parser("backup",help="Create an encrypted backup file")
    backup.add_argument("output",type=Path)
    restore=sub.add_parser("restore",help="Restore an encrypted backup (overwrites target database)")
    restore.add_argument("input",type=Path)
    restore.add_argument("--confirm-overwrite",action="store_true",help="Required confirmation before replacing database records")
    args=parser.parse_args()
    if args.command=="backup":
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(familyguard.create_backup_blob())
        print("Encrypted backup created:",args.output)
    else:
        result=familyguard.restore_backup_blob(args.input.read_bytes(),confirm_overwrite=args.confirm_overwrite)
        print("Restore complete:",result)


if __name__=="__main__":
    run()
