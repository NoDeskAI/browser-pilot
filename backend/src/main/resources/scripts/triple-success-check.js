return (function(keywords) {
  function collectText() {
    var parts = [];
    try { if (document.body && document.body.innerText) parts.push(document.body.innerText); } catch (e) {}
    try {
      var sels = ['.bili-toast','[class*="toast"]','[class*="message"]','[class*="notice"]','[class*="tip"]','[role="alert"]'];
      for (var s = 0; s < sels.length; s++) {
        var nodes = document.querySelectorAll(sels[s]);
        for (var i = 0; i < nodes.length; i++) {
          var t = (nodes[i].innerText || nodes[i].textContent || '').trim();
          if (t) parts.push(t);
        }
      }
    } catch (e) {}
    return parts.join('\n').replace(/\s+/g, ' ');
  }
  var text = collectText();
  for (var i = 0; i < keywords.length; i++) {
    if (text.indexOf(keywords[i]) >= 0) {
      return { matched: true, keyword: keywords[i], sample: text.slice(0, 220) };
    }
  }
  return { matched: false, keyword: '', sample: text.slice(0, 220) };
})(arguments[0]);
