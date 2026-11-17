// Global UI behaviour
document.addEventListener('DOMContentLoaded', function () {

  // Close the mobile navigation after tapping a link
  document.querySelectorAll('.main-nav a').forEach(function (a) {
    a.addEventListener('click', function () {
      document.body.classList.remove('nav-open');
    });
  });

  // Close mobile nav when tapping outside of it
  document.addEventListener('click', function (e) {
    if (!document.body.classList.contains('nav-open')) return;
    if (e.target.closest('.site-header')) return;
    document.body.classList.remove('nav-open');
  });

  // Auto-hide flash messages
  document.querySelectorAll('.flash').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity .5s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 500);
    }, 6000);
  });
});
