global.self = global;

const fs = require('fs');
const path = require('path');
const { parseManifest } = require('@remnote/plugin-sdk');

const manifestPath = path.join(__dirname, '..', 'public', 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const result = parseManifest(manifest);
if (!result.success) {
  console.error(result.error);
  process.exit(1);
}
console.log(`Validated ${manifestPath} with @remnote/plugin-sdk.`);
