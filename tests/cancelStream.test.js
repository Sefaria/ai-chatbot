import test from 'node:test';
import assert from 'node:assert/strict';
import { cancelStream, sendMessageStream } from '../src/lib/api.js';

test('cancel retries the startup 404 using the same message identity', async () => {
  const calls=[];
  const original=globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push(JSON.parse(options.body));
    return calls.length===1 ? new Response('{}',{status:404}) : Response.json({status:'cancelling'});
  };
  try {
    const result=await cancelStream('/api',{userId:'u',sessionId:'s',messageId:'m'});
    assert.equal(result.status,'cancelling');
    assert.deepEqual(calls[0],calls[1]);
  } finally {globalThis.fetch=original;}
});
test('cancel transport failure is not reported as a successful stop', async () => {
  const original=globalThis.fetch;
  globalThis.fetch = async () => {throw new Error('offline');};
  try {await assert.rejects(cancelStream('/api',{userId:'u',sessionId:'s',messageId:'m'}), /offline/);}
  finally {globalThis.fetch=original;}
});
test('a broken stream recovering a cancelled turn terminates as cancellation', async () => {
  const original=globalThis.fetch;
  globalThis.window={location:{href:'https://www.sefaria.org/texts'}};
  const calls=[];
  globalThis.fetch=async url=>{
    calls.push(url);
    if(url.endsWith('/stream'))return new Response('',{headers:{'Content-Type':'text/event-stream'}});
    if(url.endsWith('/recover'))return Response.json({status:'cancelled'});
    return Response.json({success:true});
  };
  try {
    await assert.rejects(sendMessageStream('/api','u','s','question',{}, {},'',false,false,{messageId:'m',timestamp:new Date().toISOString()},'en'), e=>e.code==='stream_cancelled');
    assert.equal(calls.filter(url=>url.endsWith('/recover')).length,1);
  } finally {globalThis.fetch=original; delete globalThis.window;}
});
