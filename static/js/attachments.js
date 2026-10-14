/* Attached documents picker: "+" menu -> Files or Folder, chips with remove, then submitted with the form.
   Each [data-attach] box holds: the real <input name="attachments">, two temporary pickers, a message line and a list. */
(function () {
  function ext(name) { var i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i + 1).toLowerCase(); }
  function fmt(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }

  function init(box) {
    var real = box.querySelector('input[name="attachments"]');
    var filePick = box.querySelector('[data-files]'), folderPick = box.querySelector('[data-folder]');
    var list = box.querySelector('[data-attach-list]'), msg = box.querySelector('[data-attach-msg]');
    if (!real || !filePick || !folderPick || !list || typeof DataTransfer === 'undefined') return;

    var allowed = (real.getAttribute('accept') || '').split(',').map(function (e) { return e.replace('.', '').trim(); }).filter(Boolean);
    var maxBytes = (parseFloat(real.dataset.maxMb) || 25) * 1048576;
    var maxFiles = parseInt(real.dataset.maxFiles, 10) || 50;
    var labels = real.dataset.labels || 'Excel, PDF, Image, ZIP';
    var items = [];   // {file, path, group}

    function say(lines) {
      if (!msg) return;
      msg.textContent = '';
      lines.forEach(function (t) { var d = document.createElement('div'); d.textContent = t; msg.appendChild(d); });
      msg.hidden = !lines.length;
    }

    function sync() {
      var dt = new DataTransfer();
      items.forEach(function (it) { dt.items.add(it.file); });
      real.files = dt.files;
      box.querySelectorAll('input[name="attachment_paths"]').forEach(function (n) { n.remove(); });
      items.forEach(function (it) {
        var h = document.createElement('input');
        h.type = 'hidden'; h.name = 'attachment_paths'; h.value = it.path;
        box.appendChild(h);
      });
      render();
    }

    function chip(icon, label, sub, onRemove) {
      var li = document.createElement('li'); li.className = 'attach-chip';
      var i = document.createElement('i'); i.className = 'bi ' + icon;
      var t = document.createElement('span'); t.className = 'attach-chip-name'; t.textContent = label;
      var s = document.createElement('span'); s.className = 'attach-chip-sub'; s.textContent = sub;
      var x = document.createElement('button'); x.type = 'button'; x.className = 'attach-chip-x';
      x.setAttribute('aria-label', 'Remove ' + label); x.innerHTML = '&times;';
      x.addEventListener('click', onRemove);
      [i, t, s, x].forEach(function (n) { li.appendChild(n); });
      return li;
    }

    function iconFor(name) {
      var e = ext(name);
      if (['xls', 'xlsx', 'xlsm', 'xlsb', 'csv'].indexOf(e) > -1) return 'bi-file-earmark-excel';
      if (e === 'pdf') return 'bi-file-earmark-pdf';
      if (e === 'zip') return 'bi-file-earmark-zip';
      return 'bi-file-earmark-image';
    }

    function render() {
      list.textContent = '';
      var groups = {}, order = [];
      items.forEach(function (it) {
        if (it.group) { if (!groups[it.group]) { groups[it.group] = []; order.push(it.group); } groups[it.group].push(it); }
      });
      order.forEach(function (g) {
        var size = groups[g].reduce(function (a, it) { return a + it.file.size; }, 0);
        list.appendChild(chip('bi-folder2', g, groups[g].length + ' file' + (groups[g].length === 1 ? '' : 's') + ' · ' + fmt(size),
          function () { items = items.filter(function (it) { return it.group !== g; }); sync(); }));
      });
      items.forEach(function (it) {
        if (it.group) return;
        list.appendChild(chip(iconFor(it.path), it.path, fmt(it.file.size),
          function () { items = items.filter(function (x) { return x !== it; }); sync(); }));
      });
      list.hidden = !items.length;
      box.classList.toggle('has-files', items.length > 0);
    }

    function add(fileList, fromFolder) {
      var problems = [], skippedType = 0, have = {};
      items.forEach(function (it) { have[it.path + '|' + it.file.size + '|' + it.file.lastModified] = 1; });
      Array.prototype.forEach.call(fileList, function (f) {
        var path = (fromFolder && f.webkitRelativePath) ? f.webkitRelativePath : f.name;
        if (allowed.indexOf(ext(f.name)) < 0) { skippedType++; return; }
        if (f.size > maxBytes) { problems.push(path + ' is larger than ' + fmt(maxBytes)); return; }
        var key = path + '|' + f.size + '|' + f.lastModified;
        if (have[key]) return;
        if (items.length >= maxFiles) { problems.push('Limit of ' + maxFiles + ' files reached. Put the rest in a ZIP file.'); return; }
        have[key] = 1;
        items.push({ file: f, path: path, group: (fromFolder && path.indexOf('/') > 0) ? path.split('/')[0] : '' });
      });
      if (skippedType) problems.unshift(skippedType + ' file' + (skippedType === 1 ? '' : 's') +
        ' skipped: only ' + labels + ' files are allowed.');
      say(problems.filter(function (v, i, a) { return a.indexOf(v) === i; }).slice(0, 5));
      sync();
    }

    filePick.addEventListener('change', function () { add(filePick.files, false); filePick.value = ''; });
    folderPick.addEventListener('change', function () { add(folderPick.files, true); folderPick.value = ''; });
    box.querySelectorAll('[data-pick]').forEach(function (b) {
      b.addEventListener('click', function () { (b.dataset.pick === 'folder' ? folderPick : filePick).click(); });
    });
    render();
  }

  function boot() { document.querySelectorAll('[data-attach]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
