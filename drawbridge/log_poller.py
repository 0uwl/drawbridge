"""Tails rsyslog's FIFO (see /etc/rsyslog.d/drawbridge.conf, the ompipe
action) and inserts each line as a DeviceLogEntry via the existing db.py
session machinery. Runs as its own s6 service (log-poller), sibling to
gunicorn — see Containerfile.

Must not touch the DB at import time (see db.py's module docstring) —
everything below runs inside main().
"""
import sys
import time

FIFO_PATH = '/run/rsyslog/devicelog.fifo'


def _handle_line(session_factory, line: str) -> None:
    from drawbridge.queries import add_device_log_entry, find_active_session_by_ip

    ip, _, message = line.partition(' ')
    if not message:
        ip, message = None, line

    session = session_factory()
    try:
        serial = None
        if ip:
            active = find_active_session_by_ip(session, ip)
            if active is not None:
                serial = active.serial
        add_device_log_entry(session, serial=serial, source='syslog', message=message)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    from drawbridge.main import create_app

    app = create_app()
    with app.app_context():
        session_factory = app.extensions['db_session_factory']
        while True:
            try:
                with open(FIFO_PATH) as fifo:      # blocks until rsyslog opens the write end
                    for line in fifo:                # blocks until data or writer close
                        line = line.rstrip('\n')
                        if not line:
                            continue
                        try:
                            _handle_line(session_factory, line)
                        except Exception as exc:
                            print(f'log_poller: failed to record line: {exc}', file=sys.stderr)
            except FileNotFoundError:
                time.sleep(1)  # rsyslog service hasn't mkfifo'd yet


if __name__ == '__main__':
    main()
