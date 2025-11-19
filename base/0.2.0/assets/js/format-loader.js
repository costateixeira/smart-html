// Format content loader
(function() {
  const code = document.getElementById('format-content');
  if (code && code.hasAttribute('data-src')) {
    const src = code.getAttribute('data-src');
    fetch(src)
      .then(response => response.text())
      .then(text => {
        code.textContent = text;
        Prism.highlightElement(code);
      });
  }
})();
