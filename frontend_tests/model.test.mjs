import test from "node:test";
import assert from "node:assert/strict";
import {ALGORITHMS,esc,number,share,arenaSpec} from "../app/ui/static/model.js";

test("escapes untrusted backend names",()=>assert.equal(esc('<img onerror="x">'),"&lt;img onerror=&quot;x&quot;&gt;"));
test("no fake metrics for missing data",()=>assert.equal(number(null),"—"));
test("distribution is bounded",()=>{assert.equal(share(3,12),25);assert.equal(share(0,0),0);assert.equal(share(10,2),100)});
test("arena compares exactly four supported strategies",()=>{assert.deepEqual(arenaSpec("http",50,3,"steady").algorithms,Object.keys(ALGORITHMS));assert.throws(()=>arenaSpec("http",1000,3,"steady"));assert.throws(()=>arenaSpec("http",50,11,"steady"))});
