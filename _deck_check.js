
(function () {
  function init() {
    var track = document.querySelector('.deck-track');
    if (!track || track.children.length === 0) return;
    var slides = track.children;
    var prev = document.querySelector('.deck-prev');
    var next = document.querySelector('.deck-next');
    var pos = document.querySelector('.deck-pos');
    var N = slides.length;
    function update() {
      var w = track.clientWidth;
      var i = Math.round(track.scrollLeft / w);
      if (i < 0) i = 0;
      if (i > N - 1) i = N - 1;
      if (pos) pos.textContent = (i + 1) + '/' + N;
      if (prev) prev.disabled = (i <= 0);
      if (next) next.disabled = (i >= N - 1);
    }
    function go(i) {
      if (i < 0) i = 0;
      if (i > N - 1) i = N - 1;
      var w = track.clientWidth;
      track.scrollTo({ left: i * w, behavior: 'smooth' });
    }
    if (prev) prev.addEventListener('click', function () {
      go(Math.round(track.scrollLeft / track.clientWidth) - 1);
    });
    if (next) next.addEventListener('click', function () {
      go(Math.round(track.scrollLeft / track.clientWidth) + 1);
    });
    track.addEventListener('scroll', update, { passive: true });
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { go(Math.round(track.scrollLeft / track.clientWidth) + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft')  { go(Math.round(track.scrollLeft / track.clientWidth) - 1); e.preventDefault(); }
    });
    update();
    window.addEventListener('resize', update, { passive: true });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
