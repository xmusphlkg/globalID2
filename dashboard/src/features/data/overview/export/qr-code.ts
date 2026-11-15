const qrVersion = 5;
const qrSize = qrVersion * 4 + 17;
const qrDataCodewords = 108;
const qrEccCodewords = 26;
const qrByteCapacity = 106;
const qrFormatBitsLowMask0 = 0b111011111000100;

function appendBits(bits: number[], value: number, length: number) {
  for (let index = length - 1; index >= 0; index -= 1) {
    bits.push((value >>> index) & 1);
  }
}

function gfMultiply(left: number, right: number): number {
  let x = left;
  let y = right;
  let result = 0;
  while (y > 0) {
    if ((y & 1) !== 0) result ^= x;
    x <<= 1;
    if ((x & 0x100) !== 0) x ^= 0x11d;
    y >>>= 1;
  }
  return result & 0xff;
}

function reedSolomonDivisor(degree: number): number[] {
  const result = Array.from({ length: degree }, () => 0);
  result[degree - 1] = 1;
  let root = 1;
  for (let index = 0; index < degree; index += 1) {
    for (let coef = 0; coef < degree; coef += 1) {
      result[coef] = gfMultiply(result[coef], root);
      if (coef + 1 < degree) result[coef] ^= result[coef + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function reedSolomonRemainder(data: number[], degree: number): number[] {
  const divisor = reedSolomonDivisor(degree);
  const result = Array.from({ length: degree }, () => 0);
  for (const byte of data) {
    const factor = byte ^ result.shift()!;
    result.push(0);
    for (let index = 0; index < degree; index += 1) {
      result[index] ^= gfMultiply(divisor[index], factor);
    }
  }
  return result;
}

function qrPayloadBytes(text: string): Uint8Array {
  const encoder = new TextEncoder();
  let payload = encoder.encode(text);
  if (payload.length <= qrByteCapacity) return payload;

  try {
    const url = new URL(text);
    payload = encoder.encode(`${url.origin}${url.pathname}`);
  } catch {
    payload = encoder.encode(text.slice(0, qrByteCapacity));
  }
  return payload.length <= qrByteCapacity ? payload : payload.slice(0, qrByteCapacity);
}

function qrCodewords(text: string): number[] {
  const payload = qrPayloadBytes(text);
  const bits: number[] = [];
  appendBits(bits, 0x4, 4);
  appendBits(bits, payload.length, 8);
  for (const byte of payload) appendBits(bits, byte, 8);

  const capacityBits = qrDataCodewords * 8;
  appendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);

  const data: number[] = [];
  for (let index = 0; index < bits.length; index += 8) {
    data.push(bits.slice(index, index + 8).reduce((sum, bit) => (sum << 1) | bit, 0));
  }
  for (let pad = 0xec; data.length < qrDataCodewords; pad = pad === 0xec ? 0x11 : 0xec) {
    data.push(pad);
  }
  return [...data, ...reedSolomonRemainder(data, qrEccCodewords)];
}

function setQrFunction(modules: boolean[][], reserved: boolean[][], row: number, col: number, dark: boolean) {
  modules[row][col] = dark;
  reserved[row][col] = true;
}

function drawQrFinder(modules: boolean[][], reserved: boolean[][], row: number, col: number) {
  for (let y = -1; y <= 7; y += 1) {
    for (let x = -1; x <= 7; x += 1) {
      const currentRow = row + y;
      const currentCol = col + x;
      if (currentRow < 0 || currentRow >= qrSize || currentCol < 0 || currentCol >= qrSize) continue;
      const inFinder = x >= 0 && x <= 6 && y >= 0 && y <= 6;
      const dark = inFinder && (x === 0 || x === 6 || y === 0 || y === 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4));
      setQrFunction(modules, reserved, currentRow, currentCol, dark);
    }
  }
}

function drawQrAlignment(modules: boolean[][], reserved: boolean[][], row: number, col: number) {
  for (let y = -2; y <= 2; y += 1) {
    for (let x = -2; x <= 2; x += 1) {
      const distance = Math.max(Math.abs(x), Math.abs(y));
      setQrFunction(modules, reserved, row + y, col + x, distance !== 1);
    }
  }
}

function drawQrFunctionPatterns(modules: boolean[][], reserved: boolean[][]) {
  drawQrFinder(modules, reserved, 0, 0);
  drawQrFinder(modules, reserved, 0, qrSize - 7);
  drawQrFinder(modules, reserved, qrSize - 7, 0);
  drawQrAlignment(modules, reserved, 30, 30);

  for (let index = 8; index < qrSize - 8; index += 1) {
    setQrFunction(modules, reserved, 6, index, index % 2 === 0);
    setQrFunction(modules, reserved, index, 6, index % 2 === 0);
  }
  setQrFunction(modules, reserved, qrSize - 8, 8, true);

  // Format coordinates are row/column here (the QR specification uses x/y).
  for (let index = 0; index <= 5; index += 1) setQrFunction(modules, reserved, index, 8, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  setQrFunction(modules, reserved, 7, 8, ((qrFormatBitsLowMask0 >>> 6) & 1) !== 0);
  setQrFunction(modules, reserved, 8, 8, ((qrFormatBitsLowMask0 >>> 7) & 1) !== 0);
  setQrFunction(modules, reserved, 8, 7, ((qrFormatBitsLowMask0 >>> 8) & 1) !== 0);
  for (let index = 9; index < 15; index += 1) setQrFunction(modules, reserved, 8, 14 - index, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  for (let index = 0; index < 8; index += 1) setQrFunction(modules, reserved, 8, qrSize - 1 - index, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  for (let index = 8; index < 15; index += 1) setQrFunction(modules, reserved, qrSize - 15 + index, 8, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
}

export function createQrDataUrl(text: string, size = 148): string {
  const modules = Array.from({ length: qrSize }, () => Array.from({ length: qrSize }, () => false));
  const reserved = Array.from({ length: qrSize }, () => Array.from({ length: qrSize }, () => false));
  drawQrFunctionPatterns(modules, reserved);

  const bits = qrCodewords(text).flatMap((byte) => Array.from({ length: 8 }, (_, index) => (byte >>> (7 - index)) & 1));
  let bitIndex = 0;
  let upward = true;
  for (let right = qrSize - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < qrSize; vertical += 1) {
      const row = upward ? qrSize - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const col = right - offset;
        if (reserved[row][col]) continue;
        const masked = (bits[bitIndex] ?? 0) ^ (((row + col) % 2 === 0) ? 1 : 0);
        modules[row][col] = masked === 1;
        bitIndex += 1;
      }
    }
    upward = !upward;
  }

  const viewSize = qrSize + 8;
  const rects = modules
    .flatMap((row, y) => row.map((filled, x) => (filled ? `<rect x="${x + 4}" y="${y + 4}" width="1" height="1"/>` : "")))
    .join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${viewSize} ${viewSize}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><g fill="#17211f">${rects}</g></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
