import http from 'k6/http';
import { check } from 'k6';

const baseUrl = __ENV.BASE_URL || 'http://localhost:8000';
const sourceWorkbook = open('../../samples/valid_import.xlsx', 'b');
const xlsxContentType =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export const options = {
  vus: 20,
  duration: '10s',
  discardResponseBodies: false,
  thresholds: {
    checks: ['rate==1.0'],
    http_req_failed: ['rate==0'],
  },
};

export function setup() {
  const runNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const idempotencyKey = `k6-import-${runNonce}`;
  const response = submitImport({ idempotencyKey, runNonce });

  const isCreated = check(response, {
    'setup import accepted': (result) => result.status === 202,
    'setup response contains import_id': (result) => Boolean(result.json('import_id')),
  });

  if (!isCreated) {
    throw new Error(
      `Unable to create setup import: status=${response.status}, body=${response.body}`,
    );
  }

  return {
    idempotencyKey,
    importId: response.json('import_id'),
    runNonce,
  };
}

export default function (data) {
  const response = submitImport(data);

  check(response, {
    'replay is accepted': (result) => result.status === 202,
    'replay returns the original import_id': (result) =>
      result.json('import_id') === data.importId,
  });
}

function submitImport({ idempotencyKey, runNonce }) {
  return http.post(
    `${baseUrl}/api/v1/imports`,
    {
      file: http.file(
        workbookWithRunNonce(runNonce),
        'valid_import.xlsx',
        xlsxContentType,
      ),
    },
    {
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      tags: {
        endpoint: 'create_import',
      },
    },
  );
}

function workbookWithRunNonce(runNonce) {
  // XLSX is a ZIP archive. Python's ZIP reader accepts bytes after the archive's
  // end record, so this changes only the upload fingerprint—not worksheet data.
  // It makes the first request of every k6 run a new file while all VUs replay
  // the exact same bytes for the idempotency assertion.
  const originalBytes = new Uint8Array(sourceWorkbook);
  const markerBytes = asciiBytes(`\nK6-IMPORT-IDEMPOTENCY-RUN:${runNonce}\n`);
  const result = new Uint8Array(originalBytes.length + markerBytes.length);

  result.set(originalBytes);
  result.set(markerBytes, originalBytes.length);

  return result.buffer;
}

function asciiBytes(value) {
  // TextEncoder is not present in every k6 JavaScript runtime. This marker is
  // deliberately ASCII-only, so charCodeAt gives the exact bytes we need.
  const bytes = new Uint8Array(value.length);

  for (let index = 0; index < value.length; index += 1) {
    bytes[index] = value.charCodeAt(index);
  }

  return bytes;
}
