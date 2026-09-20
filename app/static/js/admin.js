(function() {
    function showCopied(btn) {
        var old = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function() { btn.textContent = old; }, 1500);
    }

    document.addEventListener('click', function(e) {
        var toggle = e.target.closest('.menu-toggle');
        if (toggle) {
            var sb = document.getElementById('sidebar');
            if (sb) sb.classList.toggle('open');
            return;
        }

        var row = e.target.closest('.clickable-row');
        if (row && row.getAttribute('data-href')) {
            if (window.getSelection && window.getSelection().toString()) return;
            window.location = row.getAttribute('data-href');
            return;
        }

        var copyBtn = e.target.closest('[data-copy-target]');
        if (copyBtn) {
            var target = document.getElementById(copyBtn.getAttribute('data-copy-target'));
            if (target) {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(target.value).then(function() {
                        showCopied(copyBtn);
                    });
                } else {
                    target.select();
                    document.execCommand('copy');
                    showCopied(copyBtn);
                }
            }
            return;
        }

        var sidebar = document.getElementById('sidebar');
        if (sidebar && sidebar.classList.contains('open')) {
            if (!sidebar.contains(e.target) && !e.target.closest('.menu-toggle')) {
                sidebar.classList.remove('open');
            }
        }
    });

    document.addEventListener('submit', function(e) {
        var form = e.target.closest('.confirm-form');
        if (form) {
            var message = form.getAttribute('data-message') || 'Are you sure? This cannot be undone.';
            if (!window.confirm(message)) {
                e.preventDefault();
            }
        }
    });

    var drop = document.getElementById('file-drop');
    var input = document.getElementById('file');
    var nameEl = document.getElementById('file-name');
    if (drop && input && nameEl) {
        function setFileLabel() {
            if (input.files && input.files.length) {
                nameEl.textContent = input.files[0].name;
            }
        }
        input.addEventListener('change', setFileLabel);
        ['dragenter', 'dragover'].forEach(function(evt) {
            drop.addEventListener(evt, function(e) {
                e.preventDefault();
                drop.classList.add('dragging');
            });
        });
        ['dragleave', 'drop'].forEach(function(evt) {
            drop.addEventListener(evt, function(e) {
                e.preventDefault();
                drop.classList.remove('dragging');
            });
        });
        drop.addEventListener('drop', function(e) {
            if (e.dataTransfer.files && e.dataTransfer.files.length) {
                input.files = e.dataTransfer.files;
                setFileLabel();
            }
        });
    }
})();