"""Local browser interface. Run with python3 app/web.py."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import secrets

import database
from reconciliation import reconcile_directory
from process_pending import process_pending
from search import search_files
from review import review_action
from actions.validator import validate_action
from workspace import inventory, load_scope, save_scope
from jobs import JobManager, OPERATIONS, get_job, recent_jobs

STATIC = Path(__file__).parent / 'static'


def selected_root(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Enter the folder you want to organize.')
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('That folder does not exist. Check its full path.')
    return root


def inside(path, root, scope=None):
    return (Path(path).is_relative_to(root) and Path(path).resolve().is_relative_to(root)
            and (scope is None or scope.allows(path)))


def snapshot(root):
    scope = load_scope(root)
    files = [{key: row[key] for key in
              ('path', 'filename', 'category', 'description', 'status', 'is_present')}
             for row in database.get_all_files() if inside(row['path'], root, scope)]
    actions = []
    for action in database.get_pending_actions():
        if inside(action.source, root, scope) and inside(action.destination, root, scope):
            data = asdict(action)
            data['valid'], data['validation_error'] = validate_action(action, str(root))
            actions.append(data)
    return {'root': str(root), 'files': files, 'actions': actions}


def dispatch(operation, data, *, progress=None):
    root = selected_root(data.get('root'))
    if operation == 'inventory':
        scope = load_scope(root)
        return {'root': str(root), 'inventory': inventory(root, scope.exclusions if scope else None, progress=progress),
                'message': 'Select what to organize. Counts exclude the locations listed below.'}
    if operation == 'save-scope':
        save_scope(root, data.get('folders'), data.get('loose_files'), data.get('exclusions'))
        return {**snapshot(root), 'message': 'Workspace scope saved. Scan to check the selected files.'}
    scope = load_scope(root)
    message = ''
    extra = {}
    if operation == 'state':
        pass
    elif operation in ('scan', 'apply'):
        result = reconcile_directory(str(root), apply=operation == 'apply', progress=progress)
        extra['report'] = asdict(result)
        message = ('Index updated. Your files have not been moved.' if operation == 'apply'
                   else 'Scan complete. Review the changes below.')
    elif operation == 'classify':
        result = process_pending(str(root), retry_failed=data.get('retry') is True, progress=progress)
        message = (f'{result.classified} classified · {result.unsupported} unsupported · '
                   f'{result.empty} empty · {result.failed} failed · {result.skipped} skipped')
    elif operation == 'search':
        extra['results'] = [asdict(result) for result in
                            search_files(str(root), str(data.get('query', '')))]
    elif operation == 'organize':
        request = str(data.get('request', '')).strip()
        if not request:
            raise ValueError('Describe how you would like your files organized.')
        from agents.organizer import run_agent
        def persist_proposal(action):
            if inside(action.source, root, scope) and inside(action.destination, root, scope):
                database.save_action(action)
        result = run_agent(f'Allowed folder: {root}\nUser request: {request}', allowed_root=str(root),
                           progress=progress, proposal_callback=persist_proposal)
        for action in result.proposed_actions:
            if inside(action.source, root, scope) and inside(action.destination, root, scope):
                database.save_action(action)
        message = result.message
    elif operation == 'review':
        answer = data.get('decision')
        if answer not in ('y', 'n'):
            raise ValueError('Choose approve or reject.')
        action = next((action for action in database.get_pending_actions()
                       if action.id == data.get('id')), None)
        if not action or not inside(action.source, root, scope) or not inside(action.destination, root, scope):
            raise ValueError('This proposal is no longer available in the selected folder.')
        review_action(action, str(root), answer)
        message = f'Proposal {action.status}.'
        if action.error:
            message += f' {action.error}'
    else:
        raise ValueError('Unknown operation.')
    return {**snapshot(root), **extra, 'message': message}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.valid_host():
            self.send(403, b'Use the printed local URL.', 'text/plain')
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path not in assets:
            self.send(404, b'Not found', 'text/plain')
            return
        filename, kind = assets[self.path]
        content = (STATIC / filename).read_text().replace('__TOKEN__', self.server.token)
        self.send(200, content.encode(), kind)

    def do_POST(self):
        expected_origin = f'http://127.0.0.1:{self.server.server_port}'
        if (not self.valid_host() or self.headers.get('X-App-Token') != self.server.token
                or self.headers.get('Origin', expected_origin) != expected_origin):
            self.send(403, b'{"error":"Reload the dashboard to reconnect."}', 'application/json')
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('Request is too large or empty.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or not self.path.startswith('/api/'):
                raise ValueError('Invalid request.')
            operation = self.path.removeprefix('/api/')
            if operation in OPERATIONS:
                data['root'] = str(selected_root(data.get('root')))
                result = {'job': self.server.jobs.submit(operation, data)}
            elif operation == 'jobs':
                result = {'jobs': recent_jobs(str(selected_root(data.get('root'))))}
            elif operation in ('job', 'cancel-job', 'resume-job'):
                job = get_job(data.get('id'))
                if job['root'] != str(selected_root(data.get('root'))):
                    raise ValueError('Job belongs to another workspace.')
                if operation == 'cancel-job':
                    job = self.server.jobs.cancel(job['id'])
                elif operation == 'resume-job':
                    job = self.server.jobs.resume(job['id'])
                result = {'job': job}
            else:
                if operation in ('save-scope', 'review') and self.server.jobs.busy():
                    raise ValueError('Wait for the active job to finish before changing scope or reviewing moves.')
                result = dispatch(operation, data)
            self.send(200, json.dumps(result).encode(), 'application/json')
        except Exception as error:
            self.send(400, json.dumps({'error': str(error)}).encode(), 'application/json')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Run the local file organizer dashboard.')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    database.create_database()
    with HTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.jobs = JobManager(dispatch)
        server.token = secrets.token_urlsafe(32)
        print(f'Open http://127.0.0.1:{server.server_port} in your browser. Ctrl+C to stop.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.jobs.close()


if __name__ == '__main__':
    main()
