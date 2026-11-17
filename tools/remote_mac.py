"""Deployment transport. Passwords are prompted, never saved or put in argv."""
from __future__ import annotations
import argparse
import getpass
import os
import json
from pathlib import Path
import sys
import paramiko


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.2.48")
    parser.add_argument("--user", default="EasyAPI")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("session", help="Read JSON commands over stdin using one authenticated connection")
    execute = sub.add_parser("exec")
    execute.add_argument("script", help="Local shell/Python script sent over SSH stdin")
    execute.add_argument("--interpreter", default="/bin/sh")
    execute.add_argument("--timeout", type=int, default=300)
    for action in ("upload", "download"):
        command = sub.add_parser(action)
        command.add_argument("source")
        command.add_argument("destination")
    args = parser.parse_args()
    password = os.environ.get("EASYAPI_DEPLOY_PASSWORD") or getpass.getpass("SSH password: ")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    # The temporary SSH client keeps a newly seen key in memory only.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, password=password, look_for_keys=False, allow_agent=False, timeout=15)
    try:
        if args.action == "session":
            print("SSH session ready", flush=True)
            for line in sys.stdin:
                if line.strip() == "exit":
                    return 0
                try:
                    command = json.loads(line)
                    if command['action'] == 'exec':
                        source = Path(command['script']).read_bytes()
                        stdin, stdout, stderr = client.exec_command(command.get('interpreter', '/bin/sh'), timeout=command.get('timeout', 600))
                        stdin.write(source)
                        stdin.channel.shutdown_write()
                        channel = stdout.channel
                        import select
                        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
                            if channel.recv_ready():
                                sys.stdout.buffer.write(channel.recv(65536)); sys.stdout.buffer.flush()
                            if channel.recv_stderr_ready():
                                sys.stderr.buffer.write(channel.recv_stderr(65536)); sys.stderr.buffer.flush()
                            select.select([channel], [], [], 0.1)
                        status = channel.recv_exit_status()
                        print(json.dumps({'command_complete': True, 'exit_code': status}), flush=True)
                    else:
                        with client.open_sftp() as sftp:
                            if command['action'] == 'upload':
                                sftp.put(command['source'], command['destination'])
                            elif command['action'] == 'download':
                                Path(command['destination']).parent.mkdir(parents=True, exist_ok=True)
                                sftp.get(command['source'], command['destination'])
                            else:
                                raise ValueError('Unknown session command')
                        print(json.dumps({'command_complete': True, 'exit_code': 0}), flush=True)
                except Exception as error:
                    print(json.dumps({'command_complete': True, 'error': str(error)}), flush=True)
            return 0
        if args.action == "exec":
            source = Path(args.script).read_bytes()
            stdin, stdout, stderr = client.exec_command(args.interpreter, timeout=args.timeout)
            stdin.write(source)
            stdin.channel.shutdown_write()
            # Drain both streams together: Docker build/pull can fill stderr.
            channel = stdout.channel
            import select
            while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
                if channel.recv_ready():
                    sys.stdout.buffer.write(channel.recv(65536)); sys.stdout.buffer.flush()
                if channel.recv_stderr_ready():
                    sys.stderr.buffer.write(channel.recv_stderr(65536)); sys.stderr.buffer.flush()
                select.select([channel], [], [], 0.1)
            return channel.recv_exit_status()
        with client.open_sftp() as sftp:
            if args.action == "upload":
                sftp.put(args.source, args.destination)
            else:
                Path(args.destination).parent.mkdir(parents=True, exist_ok=True)
                sftp.get(args.source, args.destination)
        print(f"{args.action} completed")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
