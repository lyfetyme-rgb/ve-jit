// run.ts — TypeScript edge orchestration for 'inventory-reconciliation'.
// Contract: stdin JSON -> spawn python skill.py -> validate stdout JSON -> exit accordingly.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const input = readFileSync(0, 'utf8');
const skill = join(__dirname, 'skill.py');
const python = process.env.PYTHON ?? 'python3';

const child = spawn(python, [skill], { stdio: ['pipe', 'pipe', 'inherit'] });
let out = '';
child.stdout.on('data', (chunk) => { out += chunk; });
child.on('close', () => {
  try {
    const result = JSON.parse(out);
    if (typeof result !== 'object' || result === null || typeof result.ok !== 'boolean') {
      throw new Error('output must be a JSON object with an ok boolean');
    }
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    process.exit(result.ok ? 0 : 1);
  } catch (error) {
    process.stderr.write('skill inventory-reconciliation produced unusable output: ' + String(error) + '\n');
    process.exit(1);
  }
});
child.stdin.write(input);
child.stdin.end();
