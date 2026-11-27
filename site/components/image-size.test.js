// Tests for reading a picture's size from the head of its file. Each
// test builds only the bytes the reader looks at, so no picture has
// to live in the repo.

import assert from "node:assert/strict";
import { test } from "node:test";

import imageSize from "./image-size.js";

// ---- Helpers ----------------------------------------------------------

function bytesOf(hex) {
  return Buffer.from(hex.replace(/\s+/g, ""), "hex");
}

function text(value) {
  return Buffer.from(value, "latin1");
}

function uint16LE(value) {
  const bytes = Buffer.alloc(2);

  bytes.writeUInt16LE(value);

  return bytes;
}

function uint16BE(value) {
  const bytes = Buffer.alloc(2);

  bytes.writeUInt16BE(value);

  return bytes;
}

function uint32BE(value) {
  const bytes = Buffer.alloc(4);

  bytes.writeUInt32BE(value);

  return bytes;
}

function uint24LE(value) {
  const bytes = Buffer.alloc(3);

  bytes.writeUIntLE(value, 0, 3);

  return bytes;
}

// The twelve bytes every WebP opens with, then one chunk.
function webp(kind, body) {
  return Buffer.concat([
    text("RIFF"),
    Buffer.alloc(4),
    text("WEBP"),
    text(kind),
    Buffer.alloc(4),
    body,
    Buffer.alloc(16),
  ]);
}

// ---- Tests ------------------------------------------------------------

test("a PNG states its size in its first chunk", () => {
  const png = Buffer.concat([
    bytesOf("89504e470d0a1a0a 0000000d"),
    text("IHDR"),
    uint32BE(2560),
    uint32BE(1600),
  ]);

  assert.deepEqual(imageSize(png), { width: 2560, height: 1600 });
});

test("a GIF states its size after its name", () => {
  const gif = Buffer.concat([text("GIF89a"), uint16LE(640), uint16LE(400)]);

  assert.deepEqual(imageSize(gif), { width: 640, height: 400 });
});

test("a JPEG states its size in a frame, past other segments", () => {
  const jpeg = Buffer.concat([
    bytesOf("ffd8"),
    // An app segment, then a table whose marker sits among the
    // frame markers. Both are stepped over.
    bytesOf("ffe0 0004 0000"),
    bytesOf("ffc4 0004 0000"),
    bytesOf("ffc2 0011 08"),
    uint16BE(1600),
    uint16BE(2560),
    Buffer.alloc(12),
  ]);

  assert.deepEqual(imageSize(jpeg), { width: 2560, height: 1600 });
});

test("a lossy WebP states its size after its start code", () => {
  const body = Buffer.concat([
    bytesOf("000000 9d012a"),
    uint16LE(2560),
    uint16LE(1600),
  ]);

  assert.deepEqual(imageSize(webp("VP8 ", body)), {
    width: 2560,
    height: 1600,
  });
});

test("a lossless WebP packs its size into four bytes", () => {
  const packed = Buffer.alloc(4);

  // Each number is stored as one less, fourteen bits apiece.
  packed.writeUInt32LE((2560 - 1) | ((1600 - 1) << 14));

  const body = Buffer.concat([bytesOf("2f"), packed]);

  assert.deepEqual(imageSize(webp("VP8L", body)), {
    width: 2560,
    height: 1600,
  });
});

test("an extended WebP states its size in three bytes each", () => {
  const body = Buffer.concat([
    Buffer.alloc(4),
    uint24LE(2560 - 1),
    uint24LE(1600 - 1),
  ]);

  assert.deepEqual(imageSize(webp("VP8X", body)), {
    width: 2560,
    height: 1600,
  });
});

test("a file that is not a picture has no size", () => {
  assert.equal(imageSize(Buffer.alloc(0)), null);
  assert.equal(imageSize(text("[]\n")), null);
  assert.equal(imageSize(text("not a picture, only words".repeat(4))), null);
});
