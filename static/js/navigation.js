// Mobile navigation: keyboard access, focus restoration and an explicit backdrop.
const sidebar = document.querySelector('#staff-navigation');
const opener = document.querySelector('[data-nav-open]');
const closer = document.querySelector('[data-nav-close]');
const backdrop = document.querySelector('[data-nav-backdrop]');
const mobile = window.matchMedia('(max-width: 800px)');

function setOpen(open) {
  sidebar.classList.toggle('is-open', open);
  opener.setAttribute('aria-expanded', String(open));
  backdrop.hidden = !open;
  document.body.style.overflow = open ? 'hidden' : '';
  // Off-canvas links must not receive focus while the mobile menu is closed.
  sidebar.inert = mobile.matches && !open;
  if (open) closer.focus();
  else opener.focus();
}

opener.addEventListener('click', () => setOpen(true));
closer.addEventListener('click', () => setOpen(false));
backdrop.addEventListener('click', () => setOpen(false));
document.addEventListener('keydown', event => {
  if (!sidebar.classList.contains('is-open')) return;
  if (event.key === 'Escape') setOpen(false);
  if (event.key === 'Tab') {
    const links = [...sidebar.querySelectorAll('a[href], button')];
    const first = links[0];
    const last = links[links.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
mobile.addEventListener('change', () => {
  sidebar.classList.remove('is-open');
  sidebar.inert = mobile.matches;
  opener.setAttribute('aria-expanded', 'false');
  backdrop.hidden = true;
  document.body.style.overflow = '';
});
sidebar.inert = mobile.matches;
