import copy
from email import policy
from email.parser import BytesParser
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
spec = importlib.util.spec_from_file_location('cobros_server_test', PACKAGE / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
from cobros_state import StateError, Store

SOURCE = '''Cuenta de cobro Example Travel
NOMBRE PROVEEDOR: Example Payee
TIPO DE SERVICIO: ALOJAMIENTO
FECHA DE SERVICIO: 04 AL 07 DE MARZO DE 2026
REFERENCIA DEL CLIENTE: TEST CUSTOMER
por valor de $3.208.110,00.
'''
PROFILES = {'operators': {'example': {'key': 'example', 'legalName': 'Example Travel', 'nit': 'TEST-NIT', 'defaultPayee': 'example', 'aliases': ['Example Travel']}},
            'payees': {'example': {'key': 'example', 'displayName': 'Example Payee', 'cedulaNit': 'TEST-ID', 'bankName': 'Example Bank', 'accountType': 'Savings', 'accountNumber': 'TEST-ACCOUNT'}}}


class Request:
    def __init__(self, fn): self.fn = fn
    def execute(self): return self.fn()


class FakeDrive:
    def __init__(self):
        self.artifacts, self.calls = [], []
        self.lose_response = None
        self.export_failures = 0
        self.hide_results = False
        self.legacy_exists = False
    def files(self): return self
    def create(self, **kwargs):
        body, media = kwargs['body'], kwargs['media_body']
        return Request(lambda: self.create_file(self, body['name'], body['parents'][0], body['mimeType'], media.data, media.mimetype, body['appProperties']['cobrosEffectKey']))
    def create_file(self, drive, title, folder, mime, content, content_type, key):
        self.calls.append(mime)
        item = {'id': 'file' + str(len(self.artifacts) + 1), 'webViewLink': 'https://example.invalid/artifact', 'key': key, 'content': content}
        self.artifacts.append(item)
        if self.lose_response == mime:
            self.lose_response = None
            raise TimeoutError('fake lost response after provider creation')
        return {'id': item['id'], 'webViewLink': item['webViewLink']}
    def export(self, **kwargs):
        def run():
            if self.export_failures:
                self.export_failures -= 1
                raise TimeoutError('fake read-only export failure')
            return b'%PDF-1.7\nexample artifact'
        return Request(run)
    def get(self, **kwargs):
        return Request(lambda: next(x['content'] for x in self.artifacts if x['id'] == kwargs['fileId']))
    def list(self, **kwargs):
        def run():
            if "name='" in kwargs['q']:
                return {'files': [{'id': 'legacy'}] if self.legacy_exists else []}
            return {'files': [] if self.hide_results else [{'id': x['id'], 'webViewLink': x['webViewLink']} for x in self.artifacts if x['key'] in kwargs['q']]}
        return Request(run)


class FakeGmail:
    def __init__(self): self.items, self.calls, self.lose_response = [], 0, False
    def users(self): return self
    def drafts(self): return self
    def create(self, **kwargs):
        def run():
            self.calls += 1
            raw = kwargs['body']['message']['raw']
            message = BytesParser(policy=policy.default).parsebytes(server.base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
            item = {'id': 'draft' + str(self.calls), 'message': {'threadId': 'thread1'}, 'mime': message}
            self.items.append(item)
            if self.lose_response:
                self.lose_response = False
                raise TimeoutError('fake Gmail lost response')
            return {'id': item['id'], 'message': item['message']}
        return Request(run)
    def list(self, **kwargs):
        key = kwargs['q'].split(':', 1)[1]
        return Request(lambda: {'drafts': [{'id': x['id'], 'message': x['message']} for x in self.items if key in str(x['mime']['Message-ID'])]})


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.drive, self.gmail = FakeDrive(), FakeGmail()
        self.patch('WORKSPACE', self.root)
        self.patch('SPOOL_DIR', self.root / 'spool' / 'cobros')
        self.patch('load_config', lambda: {})
        self.patch('profile_data', lambda: copy.deepcopy(PROFILES))
        self.patch('operations_email_token', lambda c: None)
        self.patch('google_build_service', lambda c, api, *a: self.drive if api == 'drive' else self.gmail)
        self.patch('google_workspace_impersonation_user', lambda c: None)
        self.patch('render_document_html', lambda c, d, f: (server.cobros_document_html(f), 'fake-template'))
        # Exercise the real Drive request adapter without installing credentials
        # or touching the network; fake only the provider client/upload transport.
        class MediaUpload:
            def __init__(self, stream, mimetype, resumable):
                self.data, self.mimetype = stream.read(), mimetype
        google = types.ModuleType('googleapiclient')
        google_http = types.ModuleType('googleapiclient.http')
        google_http.MediaIoBaseUpload = MediaUpload
        modules = patch.dict(sys.modules, {'googleapiclient': google, 'googleapiclient.http': google_http})
        modules.start(); self.addCleanup(modules.stop)
        env = patch.dict(os.environ, {'OWLSWATCH_COBROS_DRAFT_TO': 'review@example.invalid'})
        env.start(); self.addCleanup(env.stop)
    def patch(self, name, value):
        p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
    def prepared(self):
        result = server.tool_prepare({'raw_text': SOURCE})
        self.assertEqual(result['status'], 'ready')
        return result
    def test_legitimate_prepared_record_renders_and_replays(self):
        prepared = self.prepared()
        self.assertEqual(self.prepared()['preparedId'], prepared['preparedId'])
        args = {'preparedId': prepared['preparedId']}
        first = server.tool_create_packet(args)
        self.assertEqual(first, server.tool_create_packet(args))
        self.assertEqual(len(self.drive.calls), 2)
        self.assertIn('Example Bank', self.drive.artifacts[0]['content'].decode())
        self.assertIn('TEST-ACCOUNT', self.drive.artifacts[0]['content'].decode())
        self.assertIn('3,208,110', self.drive.artifacts[0]['content'].decode())
        draft = server.tool_create_gmail_draft(args)
        self.assertEqual(draft, server.tool_create_gmail_draft(args))
        self.assertEqual(self.gmail.calls, 1)
        self.assertEqual(str(self.gmail.items[0]['mime']['To']), 'review@example.invalid')
        self.assertEqual(len(list(self.gmail.items[0]['mime'].iter_attachments())), 1)
    def test_legacy_document_cannot_be_duplicated_after_upgrade(self):
        args = {'preparedId': self.prepared()['preparedId']}
        self.drive.legacy_exists = True
        with self.assertRaises(server.ToolError) as caught: server.tool_create_packet(args)
        self.assertEqual(caught.exception.code, 'legacy_packet_requires_review')
        self.assertEqual(self.drive.calls, [])
    def test_mutable_prepared_fields_never_authorize_a_write(self):
        prepared = self.prepared()
        for changed in ['amountCop', 'payee', 'debtorNit']:
            forged = copy.deepcopy(prepared)
            forged['fields'][changed] = 'tampered'
            with self.assertRaises(server.ToolError):
                server.tool_create_packet({'preparedId': prepared['preparedId'], 'prepared': forged})
        with self.assertRaises(StateError): server.tool_create_packet({'preparedId': '0' * 32})
        with self.assertRaises(server.ToolError): server.tool_create_packet({'prepared': {'status': 'ready', 'fields': {}}})
        self.assertEqual(self.drive.calls, [])
    def test_dispute_and_override_flags_cannot_issue_record(self):
        result = server.tool_prepare({'raw_text': SOURCE + 'El valor no coincide.'})
        self.assertEqual(result['status'], 'needs_human')
        self.assertNotIn('preparedId', result)
        for extra in [{'human_override': True}, {'override_fields': {'amountCop': 1}}]:
            with self.assertRaises(server.ToolError): server.tool_prepare({'raw_text': SOURCE, **extra})
    def test_source_id_binds_fields_recipient_and_thread(self):
        source = {'account': server.gmail_account({}), 'threadId': 'thread-original', 'rawText': SOURCE,
                  'messages': [{'fromEmail': 'source@example.invalid', 'gmailMessageId': 'msg1', 'rfc822MessageId': '<test@example.invalid>'}]}
        identity = server.state_store().save_source(source)
        prepared = server.tool_prepare({'sourceId': identity})
        args = {'preparedId': prepared['preparedId']}
        server.tool_create_packet(args)
        draft = server.tool_create_gmail_draft(args)
        self.assertEqual(draft['recipient'], 'source@example.invalid')
        self.assertEqual(draft['gmailThreadId'], 'thread-original')
        with self.assertRaises(server.ToolError): server.tool_prepare({'sourceId': identity, 'raw_text': SOURCE})
        for extra in [{'to': 'attacker@example.invalid'}, {'packet': {'pdfLocalPath': '/tmp/other.pdf'}}, {'threadId': 'different'}]:
            with self.assertRaises(server.ToolError): server.tool_create_gmail_draft({**args, **extra})
    def test_same_gmail_source_cannot_change_prepared_amount(self):
        source = {'account': server.gmail_account({}), 'threadId': 'thread1', 'rawText': SOURCE, 'messages': []}
        first = server.state_store().save_source(source)
        server.tool_prepare({'sourceId': first})
        source['rawText'] = SOURCE.replace('3.208.110', '4.208.110')
        second = server.state_store().save_source(source)
        with self.assertRaises(StateError) as caught: server.tool_prepare({'sourceId': second})
        self.assertEqual(caught.exception.code, 'source_conflict')
    def test_partial_export_failure_reuses_existing_document(self):
        args = {'preparedId': self.prepared()['preparedId']}
        self.drive.export_failures = 1
        with self.assertRaises(TimeoutError): server.tool_create_packet(args)
        result = server.tool_create_packet(args)
        self.assertTrue(result['ok'])
        self.assertEqual(self.drive.calls.count('application/vnd.google-apps.document'), 1)
    def test_lost_document_response_reconciles_without_duplicate(self):
        args = {'preparedId': self.prepared()['preparedId']}
        self.drive.lose_response = 'application/vnd.google-apps.document'
        with self.assertRaises(StateError): server.tool_create_packet(args)
        self.assertTrue(server.tool_create_packet(args)['ok'])
        self.assertEqual(len(self.drive.calls), 2)
    def test_lost_pdf_response_reconciles_without_duplicate(self):
        args = {'preparedId': self.prepared()['preparedId']}
        self.drive.lose_response = 'application/pdf'
        with self.assertRaises(StateError): server.tool_create_packet(args)
        self.assertTrue(server.tool_create_packet(args)['ok'])
        self.assertEqual(len(self.drive.calls), 2)
    def test_unknown_outcome_with_no_provider_match_never_recreates(self):
        args = {'preparedId': self.prepared()['preparedId']}
        self.drive.lose_response = 'application/vnd.google-apps.document'
        with self.assertRaises(StateError): server.tool_create_packet(args)
        self.drive.hide_results = True
        for _ in range(2):
            with self.assertRaises(StateError) as caught: server.tool_create_packet(args)
            self.assertEqual(caught.exception.code, 'outcome_unknown')
        self.assertEqual(len(self.drive.calls), 1)
    def test_lost_gmail_response_reconciles_without_duplicate(self):
        args = {'preparedId': self.prepared()['preparedId']}
        server.tool_create_packet(args)
        self.gmail.lose_response = True
        with self.assertRaises(StateError): server.tool_create_gmail_draft(args)
        self.assertTrue(server.tool_create_gmail_draft(args)['ok'])
        self.assertEqual(self.gmail.calls, 1)
    def test_operations_failure_preserves_and_reuses_draft(self):
        args = {'preparedId': self.prepared()['preparedId']}
        server.tool_create_packet(args)
        self.patch('operations_email_token', lambda c: 'test-only')
        calls = []
        def intake(*args):
            calls.append(1)
            raise TimeoutError('fake outcome unknown')
        self.patch('operations_email_intake', intake)
        result = server.tool_create_gmail_draft(args)
        self.assertFalse(result['ok'])
        self.assertEqual(result['gmailDraftId'], 'draft1')
        self.assertFalse(server.tool_create_gmail_draft(args)['ok'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.gmail.calls, 1)
    def test_concurrent_worker_cannot_claim_preparation(self):
        identity = self.prepared()['preparedId']
        errors = []
        def worker():
            try: server.tool_create_packet({'preparedId': identity})
            except StateError as exc: errors.append(exc.code)
        with server.state_store().claim(identity):
            thread = threading.Thread(target=worker)
            thread.start(); thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ['workflow_busy'])
        self.assertEqual(self.drive.calls, [])
    def test_spool_tampering_blocks_attachment(self):
        args = {'preparedId': self.prepared()['preparedId']}
        packet = server.tool_create_packet(args)['packet']
        Path(packet['pdfLocalPath']).write_bytes(b'%PDF-1.7 tampered')
        with self.assertRaises(server.ToolError) as caught: server.tool_create_gmail_draft(args)
        self.assertEqual(caught.exception.code, 'attachment_changed')
        self.assertEqual(self.gmail.calls, 0)
    def test_telegram_destination_cannot_be_selected_by_model(self):
        self.patch('cobros_notify_chat_id', lambda c: 'configured-chat')
        self.patch('cobros_notify_thread_id', lambda c: 'configured-topic')
        self.patch('telegram_token', lambda c: 'test-only')
        calls = []
        self.patch('http_json', lambda *a, **k: calls.append(a[1]) or {'ok': True, 'result': {'message_id': 1}})
        for extra in [{'chat_id': 'elsewhere'}, {'message_thread_id': 'other'}]:
            with self.assertRaises(server.ToolError): server.tool_send_telegram_message({'text': 'Ready', **extra})
        self.assertEqual(calls, [])
        server.tool_send_telegram_message({'text': 'Ready'})
        self.assertEqual(calls[0]['chat_id'], 'configured-chat')
        self.assertEqual(calls[0]['message_thread_id'], 'configured-topic')
    def test_crash_marker_restarts_in_reconcile_only_mode(self):
        identity = self.prepared()['preparedId']
        with server.state_store().claim(identity) as journal: journal.save('document', None, 'attempting')
        with self.assertRaises(StateError): server.tool_create_packet({'preparedId': identity})
        self.assertEqual(self.drive.calls, [])


if __name__ == '__main__': unittest.main()
