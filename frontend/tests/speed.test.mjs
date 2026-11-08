import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient } from '@tanstack/react-query';
const nodeRequire = createRequire(import.meta.url);
const directory = fileURLToPath(new URL('.', import.meta.url));

// Transpile only trusted application modules; no browser or network dependency.
function load(relative, overrides = {}) {
  const source = fs.readFileSync(path.join(directory, '..', 'src', relative), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText;
  const compiledModule = { exports: {} };
  new Function('require', 'module', 'exports', js)(name => overrides[name] || nodeRequire(name), compiledModule, compiledModule.exports);
  return compiledModule.exports;
}

let api, client;
beforeEach(() => {
  const storage = () => {
    const values = new Map();
    return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  };
  global.sessionStorage = storage(); global.localStorage = storage();
  global.window = { dispatchEvent() {} };
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  api = load('lib/api.ts', { './query-client': { apiQueryClient: client } });
});

test('repository cache deduplicates and never crosses sessions', async () => {
  let calls = 0;
  global.fetch = async () => { calls++; return new Response(JSON.stringify({items: [{id: String(calls)}]})); };
  api.setAuthToken('alice');
  const [one, two] = await Promise.all([api.listRepositories(), api.listRepositories()]);
  assert.equal(calls, 1); assert.deepEqual(one, two);
  assert.deepEqual(await api.listRepositories(), one); assert.equal(calls, 1);
  assert.ok(!JSON.stringify(client.getQueryCache().getAll().map(q => q.queryKey)).includes('alice'));
  api.setAuthToken('bob');
  assert.equal((await api.listRepositories()).items[0].id, '2');
  assert.equal(calls, 2);
});

test('stale data returns immediately while revalidation remains pending', async () => {
  api.setAuthToken('alice');
  const options = api.repositoryQuery();
  client.setQueryData(options.queryKey, {items: [{id: 'cached'}]}, {updatedAt: Date.now() - 20_000});
  let release;
  global.fetch = () => new Promise(resolve => { release = resolve; });
  assert.equal((await api.listRepositories()).items[0].id, 'cached');
  assert.equal(typeof release, 'function');
  release(new Response(JSON.stringify({items: [{id: 'fresh'}]})));
  await client.getQueryCache().find({queryKey: options.queryKey}).promise;
  assert.equal(client.getQueryData(options.queryKey).items[0].id, 'fresh');
});

test('late unauthorized response cannot sign out a replacement session', async () => {
  api.setAuthToken('alice');
  let release;
  global.fetch = () => new Promise(resolve => { release = resolve; });
  const pending = api.getMe();
  api.setAuthToken('bob');
  release(new Response('{}', {status: 401}));
  await assert.rejects(pending, /unauthorized/i);
  assert.equal(api.getAuthToken(), 'bob');
});

test('current unauthorized response clears cache and session', async () => {
  api.setAuthToken('alice');
  client.setQueryData(api.repositoryQuery().queryKey, {items: [{id: 'private'}]});
  global.fetch = async () => new Response('{}', {status: 401});
  await assert.rejects(api.getMe());
  assert.equal(api.getAuthToken(), null); assert.equal(client.getQueryCache().getAll().length, 0);
});

test('repository selector exposes loading and recovery feedback', () => {
  const { RepositorySelector } = load('components/RepositorySelector.tsx');
  const loading = renderToStaticMarkup(React.createElement(RepositorySelector, {
    repositories: [], selectedRepoId: '', onSelect() {}, loading: true,
  }));
  assert.match(loading, /Loading accessible repositories/); assert.match(loading, /disabled/);
  const error = renderToStaticMarkup(React.createElement(RepositorySelector, {
    repositories: [], selectedRepoId: '', onSelect() {}, loading: false,
    error: 'Unable to load repositories', onRefresh() {},
  }));
  assert.match(error, /Unable to load repositories/); assert.match(error, /Refresh Repositories/);
});
