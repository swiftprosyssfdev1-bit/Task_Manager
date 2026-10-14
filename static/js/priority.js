// Replaces the browser's own priority list with a clean dropdown that shows a coloured dot per priority.
// The real <select> stays in the page (hidden), so the form posts exactly as before.
(function () {
  function dot(value) {
    var d = document.createElement('span');
    d.className = 'prio-dot';
    d.setAttribute('data-value', value || '');
    return d;
  }

  function build(sel) {
    var wrap = document.createElement('div');
    wrap.className = 'prio-dd';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'form-select prio-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    var menu = document.createElement('ul');
    menu.className = 'prio-menu';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;

    var label = sel.id ? document.querySelector('label[for="' + sel.id + '"]') : null;
    if (label) {
      label.id = label.id || sel.id + '_label';
      btn.setAttribute('aria-labelledby', label.id);
      label.addEventListener('click', function (e) { e.preventDefault(); btn.focus(); });
    }

    var items = [], active = -1;
    Array.prototype.forEach.call(sel.options, function (o, i) {
      var li = document.createElement('li');
      li.className = 'prio-item';
      li.setAttribute('role', 'option');
      li.appendChild(dot(o.value));
      li.appendChild(document.createTextNode(o.text));
      li.addEventListener('mouseenter', function () { setActive(i); });
      li.addEventListener('mousedown', function (e) { e.preventDefault(); });
      li.addEventListener('click', function () { choose(i); });
      menu.appendChild(li);
      items.push(li);
    });

    function render() {
      var o = sel.options[sel.selectedIndex];
      btn.innerHTML = '';
      btn.appendChild(dot(o ? o.value : ''));
      btn.appendChild(document.createTextNode(o ? o.text : ''));
      items.forEach(function (li, i) { li.setAttribute('aria-selected', i === sel.selectedIndex ? 'true' : 'false'); });
    }
    function setActive(i) {
      active = i;
      items.forEach(function (li, k) { li.classList.toggle('is-active', k === i); });
    }
    function open() {
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      setActive(Math.max(sel.selectedIndex, 0));
    }
    function close() {
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }
    function choose(i) {
      sel.selectedIndex = i;
      sel.dispatchEvent(new Event('change', {bubbles: true}));
      render();
      close();
      btn.focus();
    }

    btn.addEventListener('click', function () { menu.hidden ? open() : close(); });
    btn.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (menu.hidden) { open(); return; }
        setActive((active + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length);
      } else if ((e.key === 'Enter' || e.key === ' ') && !menu.hidden) {
        e.preventDefault();
        choose(active);
      } else if (e.key === 'Escape' && !menu.hidden) {
        e.preventDefault();
        close();
      } else if (e.key === 'Tab') {
        close();
      }
    });
    document.addEventListener('click', function (e) { if (!wrap.contains(e.target)) close(); });
    sel.addEventListener('change', render);  // keeps the dropdown in step if the value is set from elsewhere

    sel.hidden = true;
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    wrap.appendChild(sel);
    render();
  }

  document.querySelectorAll('select.priority-select').forEach(build);
})();
