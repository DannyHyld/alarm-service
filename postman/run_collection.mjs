// Kører Postman-collectionen uden Postman (kun Node 18+, ingen pakker).
// Bruges til at tjekke collectionen; i videoen bruges Postman selv.
//
//   node postman/run_collection.mjs lokal
//   node postman/run_collection.mjs produktion
//
// Nøgler læses fra miljøvariablerne DEVICE_KEY, OPERATOR_KEY og MAILPIT_PASSWORD.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const envName = process.argv[2] || "lokal";
const collection = JSON.parse(readFileSync(join(here, "alarm-service.postman_collection.json"), "utf8"));
const env = JSON.parse(readFileSync(join(here, `${envName}.postman_environment.json`), "utf8"));

const vars = Object.fromEntries(env.values.map(v => [v.key, v.value]));
vars.device_key = process.env.DEVICE_KEY || vars.device_key;
vars.operator_key = process.env.OPERATOR_KEY || vars.operator_key;
vars.mailpit_password = process.env.MAILPIT_PASSWORD || vars.mailpit_password;
const collVars = Object.fromEntries((collection.variable || []).map(v => [v.key, v.value]));

const sub = s => String(s).replace(/\{\{(\w+)\}\}/g, (_, k) => String(collVars[k] ?? vars[k] ?? ""));

// Minimal udgave af Postmans pm-objekt: kun det, collectionen bruger.
function expectFn(actual) {
  const api = {
    eql: v => assert.deepStrictEqual(actual, v),
    include: v => assert.ok(actual.includes(v), `${JSON.stringify(actual)} indeholder ikke ${JSON.stringify(v)}`),
    a: t => assert.equal(typeof actual, t),
    get null() { assert.equal(actual, null); return true; },
    get true() { assert.equal(actual, true); return true; },
  };
  return { to: { eql: api.eql, include: api.include, be: { a: api.a, get null() { return api.null; }, get true() { return api.true; } } } };
}

let passed = 0, failed = 0;
function flatten(items) { return items.flatMap(i => (i.item ? flatten(i.item) : [i])); }

for (const item of flatten(collection.item)) {
  const r = item.request;
  const headers = Object.fromEntries(r.header.map(h => [h.key, sub(h.value)]));
  if (r.auth?.type === "basic") {
    const get = k => sub(r.auth.basic.find(b => b.key === k).value);
    if (get("username")) headers.Authorization = "Basic " + Buffer.from(`${get("username")}:${get("password")}`).toString("base64");
  }
  const res = await fetch(sub(r.url.raw), { method: r.method, headers, body: r.body ? sub(r.body.raw) : undefined });
  const text = await res.text();
  let json; try { json = JSON.parse(text); } catch { json = undefined; }
  const pm = {
    response: { code: res.status, json: () => json, to: { have: { status: c => assert.equal(res.status, c) } } },
    expect: expectFn,
    collectionVariables: { set: (k, v) => { collVars[k] = v; }, get: k => collVars[k] },
    test: (name, fn) => {
      try { fn(); passed++; console.log(`  OK    ${name}`); }
      catch (e) { failed++; console.log(`  FEJL  ${name}: ${e.message.split("\n")[0]}`); }
    },
  };
  console.log(`${item.name}  [${res.status}]`);
  for (const ev of item.event || []) new Function("pm", ev.script.exec.join("\n"))(pm);
}
console.log(`\n${passed} tests bestået, ${failed} fejlet (${envName})`);
process.exit(failed ? 1 : 0);
