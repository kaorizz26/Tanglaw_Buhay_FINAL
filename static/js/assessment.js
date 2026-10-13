// Program links work without JS; enhancement switches the existing panels locally.
const cards = [...document.querySelectorAll('[data-program-select]')];
const panels = [...document.querySelectorAll('[data-program-panel]')];

function selectProgram(card, updateAddress = true) {
  const code = card.dataset.programSelect;
  for (const item of cards) {
    const selected = item === card;
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
  }
  for (const panel of panels) panel.hidden = panel.dataset.programPanel !== code;
  if (updateAddress) window.history.replaceState(null, '', card.href);
}

for (const card of cards) {
  card.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selectProgram(card);
  });
  card.addEventListener('keydown', event => {
    const index = cards.indexOf(card);
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % cards.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + cards.length) % cards.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = cards.length - 1;
    else return;
    event.preventDefault();
    cards[next].focus();
    selectProgram(cards[next]);
  });
}
const initialCard = cards.find(card => card.getAttribute('aria-selected') === 'true');
if (initialCard) selectProgram(initialCard, false);

window.addEventListener('popstate', () => {
  const code = new URL(window.location.href).searchParams.get('program') || '4Ps';
  const card = cards.find(item => item.dataset.programSelect === code);
  if (card) selectProgram(card, false);
});
