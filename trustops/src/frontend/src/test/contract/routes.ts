/**
 * Helpers for the contract tests: the routes the web calls, and the routes the backend serves.
 *
 * A route is `METHOD /path/{id}` with every path parameter spelled `{id}`, so the web's `:itemId`,
 * its builders' arguments and FastAPI's `{case_id}` all compare equal. The web's paths come from
 * `api/endpoints.ts` (the one place they are spelled). That file has no methods, so each template
 * takes the methods of the MSW handlers that serve it (the executable contract, roles included); a
 * template no handler serves, which is a builder nothing calls, is `ANY`.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { paths } from '@/api/endpoints';
import { handlers } from '@/mocks/handlers';

/** The merged OpenAPI the backend exports (`make openapi`): every service under its public prefix. */
const API_JSON = join(dirname(fileURLToPath(import.meta.url)), '../../../../../docs/architecture/openapi/api.json');

const PARAM = '__P__';
const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'];

/** Every path parameter spelled the same: `:itemId`, `{case_id}` and the builders' placeholder. */
export function normalize(path: string): string {
  return path.replace(new RegExp(PARAM, 'g'), '{id}').replace(/:[A-Za-z_]+/g, '{id}').replace(/\{[^}]*\}/g, '{id}');
}

function collect(node: unknown, out: string[]): void {
  if (typeof node === 'string') {
    out.push(node);
  } else if (typeof node === 'function') {
    const build = node as (...args: string[]) => string;
    out.push(build(...Array.from({ length: build.length }, () => PARAM)));
  } else if (node && typeof node === 'object') {
    Object.values(node).forEach((value) => collect(value, out));
  }
}

/** The distinct path templates of `api/endpoints.ts`. */
export function webTemplates(): string[] {
  const raw: string[] = [];
  collect(paths, raw);
  return [...new Set(raw.map(normalize))].sort();
}

/** Method -> templates, from the MSW handlers; the mock-only control routes are left out. */
function handlerMethods(): Map<string, Set<string>> {
  const byTemplate = new Map<string, Set<string>>();
  for (const handler of handlers) {
    const { method, path } = handler.info;
    if (typeof path !== 'string' || path.includes('/__mock/')) continue;
    const template = normalize(path);
    const methods = byTemplate.get(template) ?? new Set<string>();
    methods.add(String(method).toUpperCase());
    byTemplate.set(template, methods);
  }
  return byTemplate;
}

/** The MSW handler paths that are not in `endpoints.ts` (a route spelled outside the one file). */
export function handlerTemplatesOutsideEndpoints(): string[] {
  const known = new Set(webTemplates());
  return [...handlerMethods().keys()].filter((t) => !known.has(t)).sort();
}

/** `METHOD /path/{id}` for every route the web calls; `ANY` where no handler names a method. */
export function webRoutes(): string[] {
  const byTemplate = handlerMethods();
  return webTemplates()
    .flatMap((template) => [...(byTemplate.get(template) ?? ['ANY'])].map((method) => `${method} ${template}`))
    .sort();
}

/** `METHOD /path/{id}` for every operation the backend's OpenAPI lists, plus `ANY /path/{id}` per path. */
export function servedRoutes(): Set<string> {
  const doc = JSON.parse(readFileSync(API_JSON, 'utf8')) as { paths: Record<string, Record<string, unknown>> };
  const served = new Set<string>();
  for (const [path, item] of Object.entries(doc.paths)) {
    const template = normalize(path);
    served.add(`ANY ${template}`);
    for (const method of Object.keys(item)) {
      if (HTTP_METHODS.includes(method)) served.add(`${method.toUpperCase()} ${template}`);
    }
  }
  return served;
}
