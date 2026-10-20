// Pre-compress built assets; the bridge serves the .br/.gz sibling when the client accepts it.
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { brotliCompressSync, gzipSync, constants } from 'node:zlib';
const dir = 'dist/assets';
for (const name of readdirSync(dir).filter((n) => /\.(js|css|svg|json)$/.test(n))) {
  const data = readFileSync(join(dir, name));
  writeFileSync(join(dir, `${name}.br`), brotliCompressSync(data, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }));
  writeFileSync(join(dir, `${name}.gz`), gzipSync(data, { level: 9 }));
}
