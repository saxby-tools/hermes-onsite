// Reads a picture's width and height from the first bytes of its
// file, for PNG, GIF, JPEG and WebP.
//
// The slideshow needs both numbers on every <img>, so the page keeps
// its shape while the pictures load. Reading them here means nobody
// types them into the manifest, and nobody gets them wrong.
//
// This is owned code in place of a package. Each format keeps its
// size in a fixed, documented place, so it is short. It reads the
// size the file states and nothing else: a JPEG that a camera marked
// as turned on its side comes back unturned. Screenshots are never
// marked that way.

// ---- Constants --------------------------------------------------------

const PNG_MAGIC = "89504e470d0a1a0a";

const GIF_MAGICS = ["GIF87a", "GIF89a"];

const JPEG_MAGIC = "ffd8";

// A JPEG is a run of segments, each led by 0xff and a marker byte.
const JPEG_LEAD = 0xff;

// The segments that state the size: every "start of frame" marker
// from 0xc0 to 0xcf, less the three in that range that are tables.
const JPEG_FRAME_FIRST = 0xc0;

const JPEG_FRAME_LAST = 0xcf;

const JPEG_TABLES = [0xc4, 0xc8, 0xcc];

// WebP comes in three kinds, and each states its size its own way.
const WEBP_LOSSY = "VP8 ";

const WEBP_LOSSLESS = "VP8L";

const WEBP_EXTENDED = "VP8X";

// ---- PNG --------------------------------------------------------------

// The first chunk is always IHDR, and it opens with the size.
function pngSize(bytes) {
  if (bytes.length < 24) {
    return null;
  }

  if (bytes.toString("hex", 0, 8) !== PNG_MAGIC) {
    return null;
  }

  return {
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

// ---- GIF --------------------------------------------------------------

function gifSize(bytes) {
  if (bytes.length < 10) {
    return null;
  }

  if (!GIF_MAGICS.includes(bytes.toString("latin1", 0, 6))) {
    return null;
  }

  return {
    width: bytes.readUInt16LE(6),
    height: bytes.readUInt16LE(8),
  };
}

// ---- JPEG -------------------------------------------------------------

function isJpegFrame(marker) {
  if (marker < JPEG_FRAME_FIRST) {
    return false;
  }

  if (marker > JPEG_FRAME_LAST) {
    return false;
  }

  return !JPEG_TABLES.includes(marker);
}

// Steps from segment to segment until one states the size. Each
// segment says how long it is, so nothing in between is read.
function jpegSize(bytes) {
  if (bytes.toString("hex", 0, 2) !== JPEG_MAGIC) {
    return null;
  }

  let offset = 2;

  while (offset + 9 <= bytes.length) {
    if (bytes[offset] !== JPEG_LEAD) {
      return null;
    }

    if (isJpegFrame(bytes[offset + 1])) {
      return {
        width: bytes.readUInt16BE(offset + 7),
        height: bytes.readUInt16BE(offset + 5),
      };
    }

    offset += 2 + bytes.readUInt16BE(offset + 2);
  }

  return null;
}

// ---- WebP -------------------------------------------------------------

// Fourteen bits each, after a three-byte tag and a three-byte code.
function webpLossySize(bytes) {
  return {
    width: bytes.readUInt16LE(26) & 0x3fff,
    height: bytes.readUInt16LE(28) & 0x3fff,
  };
}

// Fourteen bits each, packed end to end, each one less than the size.
function webpLosslessSize(bytes) {
  const bits = bytes.readUInt32LE(21);

  return {
    width: (bits & 0x3fff) + 1,
    height: ((bits >>> 14) & 0x3fff) + 1,
  };
}

// Three bytes each, each one less than the size.
function webpExtendedSize(bytes) {
  return {
    width: bytes.readUIntLE(24, 3) + 1,
    height: bytes.readUIntLE(27, 3) + 1,
  };
}

function webpSize(bytes) {
  if (bytes.length < 30) {
    return null;
  }

  if (bytes.toString("latin1", 0, 4) !== "RIFF") {
    return null;
  }

  if (bytes.toString("latin1", 8, 12) !== "WEBP") {
    return null;
  }

  const kind = bytes.toString("latin1", 12, 16);

  if (kind === WEBP_LOSSY) {
    return webpLossySize(bytes);
  }

  if (kind === WEBP_LOSSLESS) {
    return webpLosslessSize(bytes);
  }

  if (kind === WEBP_EXTENDED) {
    return webpExtendedSize(bytes);
  }

  return null;
}

// ---- Size -------------------------------------------------------------

// `bytes` is a Buffer of the whole file. Returns `{ width, height }`
// in pixels, or null when the file is none of the four formats.
export default function imageSize(bytes) {
  return (
    pngSize(bytes) ??
    gifSize(bytes) ??
    jpegSize(bytes) ??
    webpSize(bytes)
  );
}
