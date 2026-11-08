export function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function hasPdfMagic(bytes) {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return u.length > 5 && u[0] === 0x25 && u[1] === 0x50 && u[2] === 0x44 && u[3] === 0x46 && u[4] === 0x2d;
}

export function looksLikePdfUrl(url) {
  try {
    const u = new URL(url);
    return /\.pdf$/i.test(u.pathname) || /^\/(?:pdf|doi\/pdf|doi\/epdf|content\/pdf)\//i.test(u.pathname);
  } catch {
    return false;
  }
}
