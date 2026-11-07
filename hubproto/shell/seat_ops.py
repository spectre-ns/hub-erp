"""The cog's seat performing edit operations live in the editor (Collabora, through UNO commands), and the check
after: once the session has saved, the hub verifies each operation in the file and finishes any that did not take.

Live, only what is atomic or proven safe: replacing or deleting text (find and replace all) and inserting headings
and paragraphs at the very start or end of the document; in a sheet, setting cells and appending rows. Positioning
the cursor at a paragraph found by search is not reliable from outside the editor (whatever stays selected is typed
over), so inserting after/before a paragraph, deleting paragraphs and sections, tables, lists, row moves and
rewrites are done by the hub in the file, and the editor reloads."""

SEAT_OPS_JS = r"""
window.HubSeatOps = (function () {
  function isPlainLine(t) { return !/^\s*(\||[-*•]\s|\d+[.)]\s|---\s*$)/.test(t); }
  function liveCapable(edit) {
    var ops = edit.ops || [];
    if (!ops.length) return false;
    if (edit.kind === 'sheet') return ops.every(function (o) { return o.op === 'set' || (o.op === 'append_rows' && o.rows && edit.start_row); });
    return ops.every(function (o) {
      if (o.op === 'replace' || o.op === 'delete') return o.op === 'delete' || (o['with'] !== null && o['with'] !== undefined);
      if (o.op === 'insert' && (o.at === 'start' || o.at === 'end')) return (o.lines || []).every(isPlainLine);
      return false;
    });
  }
  function styleArg(name) { return { Style: { type: 'string', value: name }, FamilyName: { type: 'string', value: 'ParagraphStyles' } }; }
  function search(uno, text, replace, command) {
    var a = { 'SearchItem.SearchString': { type: 'string', value: text }, 'SearchItem.Backward': { type: 'boolean', value: false },
              'SearchItem.Command': { type: 'long', value: command } };
    if (replace !== null) a['SearchItem.ReplaceString'] = { type: 'string', value: replace };
    uno('.uno:ExecuteSearch', a);
  }
  // One line of the writing markup typed into the current paragraph; returns how long to wait before the next step.
  function typeLine(uno, line) {
    var t = line.trim(), style = 'Default Paragraph Style';
    if (t.indexOf('### ') === 0) { style = 'Heading 3'; t = t.slice(4); }
    else if (t.indexOf('## ') === 0) { style = 'Heading 2'; t = t.slice(3); }
    else if (t.indexOf('# ') === 0) { style = 'Heading 1'; t = t.slice(2); }
    uno('.uno:StyleApply', styleArg(style));
    var d = 60;
    t.split(/(\*\*[^*]+\*\*)/).forEach(function (pt) {
      if (!pt) return;
      var bold = pt.length > 4 && pt.slice(0, 2) === '**' && pt.slice(-2) === '**', tx = bold ? pt.slice(2, -2) : pt;
      setTimeout(function () { if (bold) uno('.uno:Bold'); uno('.uno:InsertText', { Text: { type: 'string', value: tx } }); if (bold) setTimeout(function () { uno('.uno:Bold'); }, 60); }, d);
      d += 120;
    });
    return d + 100;
  }
  function seq(steps, done) {
    var i = 0;
    (function next() { if (i >= steps.length) { done(); return; } var w = steps[i++](); setTimeout(next, typeof w === 'number' ? w : 160); })();
  }
  function textSteps(o, uno) {
    var s = [], lines = (o.lines || []).filter(function (x) { return x.trim(); });
    var typeAll = function (paraFirst) {
      lines.forEach(function (ln, k) {
        if (paraFirst || k > 0) s.push(function () { uno('.uno:InsertPara'); return 140; });
        s.push(function () { return typeLine(uno, ln); });
      });
    };
    if (o.op === 'insert' && o.at === 'end') {
      s.push(function () { uno('.uno:GoToEndOfDoc'); return 120; });
      typeAll(true);
    } else if (o.op === 'insert' && o.at === 'start') {  // open an empty paragraph above the first and write there
      s.push(function () { uno('.uno:GoToStartOfDoc'); return 120; });
      s.push(function () { uno('.uno:InsertPara'); return 140; });
      s.push(function () { uno('.uno:GoUp'); return 140; });
      typeAll(false);
    } else if (o.op === 'replace' || o.op === 'delete') {
      var w = o.op === 'delete' ? '' : (o['with'] || '');
      s.push(function () { search(uno, o.target, w, 3); return 450; });
    }
    return s;
  }
  function colName(n) { var s = ''; n++; while (n) { var r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; }
  function sheetSteps(o, uno, edit) {
    var s = [];
    var put = function (addr, val) {
      s.push(function () { uno('.uno:GoToCell', { ToPoint: { type: 'string', value: addr } }); return 100; });
      s.push(function () { uno('.uno:EnterString', { StringName: { type: 'string', value: String(val) } }); return 130; });
    };
    if (o.op === 'set') put(o.cell, o.value || '');
    if (o.op === 'append_rows') {
      var r0 = edit.start_row;
      (o.rows || []).forEach(function (cells, i) { cells.forEach(function (v, c) { put(colName(c) + (r0 + i), v); }); });
      edit.start_row = r0 + (o.rows || []).length;
    }
    return s;
  }
  function run(edit, uno, done) {
    var steps = [];
    (edit.ops || []).forEach(function (o) { steps = steps.concat(edit.kind === 'sheet' ? sheetSteps(o, uno, edit) : textSteps(o, uno)); });
    if (edit.kind !== 'sheet') steps.push(function () { uno('.uno:GoToEndOfDoc'); return 100; });
    seq(steps, done);
  }
  // After the live edit: save, wait for the new version, then the hub checks every operation and finishes the rest.
  function verifyAfterSave(o, cb) {
    var get = function () { return fetch(o.repliesUrl).then(function (r) { return r.json(); }); };
    get().then(function (j0) {
      var v0 = j0.version, tries = 0;
      o.post({ MessageId: 'Action_Save', Values: { DontTerminateEdit: true, DontSaveIfUnmodified: false, Notify: true } });
      var wait = function () {
        tries++;
        get().then(function (j) {
          if (j.version > v0 || tries >= 10) {
            var fd = new FormData(); fd.append('as', o.user); fd.append('doc', o.loc); fd.append('id', o.id);
            fetch(o.verifyUrl, { method: 'POST', body: fd }).then(function (r) { return r.json(); }).then(cb).catch(function () { cb({ error: true }); });
          } else setTimeout(wait, 1500);
        }).catch(function () { setTimeout(wait, 1500); });
      };
      setTimeout(wait, 1500);
    }).catch(function () { cb({ error: true }); });
  }
  return { liveCapable: liveCapable, run: run, verifyAfterSave: verifyAfterSave };
})();
"""
