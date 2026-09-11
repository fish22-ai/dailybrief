(async () => {
  const track = document.querySelector('.deck-track');
  const slides = [...track.children];
  const ctl = document.querySelector('.deck-ctl');
  const N = slides.length;
  const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const r1 = n => Math.round(n);
  const vh = window.innerHeight;
  const heights = slides.map(s => s.offsetHeight);
  const maxH = Math.max(...heights), minH = Math.min(...heights);
  // 停在最短那张卡上、并滚到页面最底，看视口下方还剩多少内容
  const short = heights.indexOf(minH);
  track.scrollLeft = short * track.clientWidth;
  await frame();
  document.scrollingElement.scrollTop = 1e9;
  await frame();
  const se = document.scrollingElement;
  const D = r1(document.documentElement.scrollHeight);
  const S = r1(se.scrollTop);
  const cardBottom = r1(slides[short].getBoundingClientRect().bottom);
  return {
    各卡高: heights,
    最高: maxH, 最矮: minH, 差: maxH - minH,
    视口高: vh,
    文档高_最长卡时: D + (maxH - minH),
    文档高_最矮卡时: D,
    停在最矮卡且滚到底: {
      scrollY: S,
      卡底在视口y: cardBottom,
      卡底以下到屏幕底: r1(vh - cardBottom),
      文档高: D,
      到底了: S + vh >= D - 1,
    },
  };
})()
