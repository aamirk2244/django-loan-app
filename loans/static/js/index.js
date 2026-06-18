// Quick helper function to extract Django's security CSRF token
function getCookie(name) {
  let value = "; " + document.cookie;
  let parts = value.split("; " + name + "=");
  if (parts.length === 2) return decodeURIComponent(parts.pop().split(";").shift());
}

// DELEGATED LISTENER: This catches clicks on the button even if it's loaded dynamically later!
// Quick helper function to extract Django's security CSRF token
function getCookie(name) {
  let value = "; " + document.cookie;
  let parts = value.split("; " + name + "=");
  if (parts.length === 2) return decodeURIComponent(parts.pop().split(";").shift());
}

// DELEGATED LISTENER: Catches the click on any dynamically loaded panel
$(document).on('click', '#fetchKiborBtn', function(ev) {
  ev.preventDefault();
  
  var $btn = $(this);
  var $spinner = $('#kiborSpinner');
  var $container = $('#kiborResult');
  var pollId = null;

  // 1. UI Setup
  $btn.prop('disabled', true);
  $spinner.show();
  $container.html('<div class="text-muted">Starting scraper backend...</div>');

  // Helper function to render log streams with color heuristics
  function renderLogLines(lines) {
    $container.empty();
    if (!lines || !lines.length) {
      $container.html('<div class="text-muted">No logs yet.</div>');
      return;
    }

    var $list = $('<div class="list-group"></div>');
    lines.forEach(function(line) {
      var $item = $('<div class="list-group-item py-1"></div>').text(line);
      
      // Heuristics for text color highlighting
      if (/\u2713|Saved|Done|Already downloaded/i.test(line)) {
        $item.addClass('text-success');
      } else if (/\u2717|Failed|Error|failed|✗/i.test(line)) {
        $item.addClass('text-danger');
      } else if (/skip|Already downloaded|skipping/i.test(line)) {
        $item.addClass('text-muted');
      }
      $list.append($item);
    });

    $container.append($list);
    $container.scrollTop($container[0].scrollHeight); // Auto-scroll to bottom
  }

  // Polling loop engine
  function poll() {
    $.getJSON('/scrape/status/', function(s) {
      if (s && s.log) {
        renderLogLines(s.log);
      } else {
        $container.html('<div class="text-muted">No logs yet.</div>');
      }

      // Check if background worker finished
      if (!s.running) {
        if (pollId) clearInterval(pollId);
        $spinner.hide();
        $btn.prop('disabled', false);

        // Fetch finalized files list from Django endpoint
        $.getJSON('/scrape/files/', function(f) {
          if (f && typeof f.count === 'number') {
            var $info = $('<div class="mt-2 fw-bold text-dark"></div>').html('Done. PDFs found: ' + f.count);
            $container.append($info);

            if (f.files && f.files.length) {
              var $filesList = $('<ul class="list-unstyled small mt-2"></ul>');
              f.files.forEach(function(fname) {
                var $li = $('<li class="mb-1"></li>');
                var $a = $('<a></a>')
                  .attr('href', '/static/data/kibor_files/' + encodeURIComponent(fname))
                  .attr('target', '_blank')
                  .text(fname);
                $li.append($a);
                $filesList.append($li);
              });
              $container.append($filesList);
            }
          }
        });
      }
    }).fail(function() {
      if (pollId) clearInterval(pollId);
      $spinner.hide();
      $btn.prop('disabled', false);
      $container.html('<div class="text-danger">Error polling scraper status.</div>');
    });
  }

  // 2. Fire the AJAX POST call to start the Django background operation
  $.ajax({
    url: '/fetch-kibor/',
    method: 'POST',
    headers: { 'X-CSRFToken': getCookie('csrftoken') },
    success: function(data) {
      // Handles both your 'ok' boolean format or 'started' string status format safely
      if (data && (data.ok || data.status === 'started')) {
        $container.html('<div class="text-muted">Scraper started — waiting for progress...</div>');
        
        // Start polling immediate execution + setup interval hook
        poll();
        pollId = setInterval(poll, 2500);
      } else {
        $container.html('<div class="text-danger">Backend error: ' + (data.error || 'Unknown failure') + '</div>');
        $btn.prop('disabled', false);
        $spinner.hide();
      }
    },
    error: function(xhr) {
      $container.html('<div class="text-danger">Failed to start scraper (Status: ' + xhr.status + ').</div>');
      $btn.prop('disabled', false);
      $spinner.hide();
    }
  });
});



// DELEGATED LISTENER: Catches the click for the Yearly KIBOR process
$(document).on('click', '#addYearlyKiborBtn', function(ev) {
  ev.preventDefault();

  var $btn = $(this);
  var origText = $btn.text();
  
  // Find target display container safely (fallback to layout panel if specific result div isn't built)
  var $target = $('#addYearlyResult').length ? $('#addYearlyResult') : $('#panel-add-yearly-kibor');

  if (!$target.length) return;

  // 1. Update UI to Loading State
  $btn.prop('disabled', true).text('Fetching...');
  $target.empty();

  // 2. Fire the GET request to your Django uniform route pattern
  $.getJSON('/fetch-yearly-kibor/', function(data) {
    // Reset button asset states
    $btn.prop('disabled', false).text(origText);

    // Check both standard boolean status parameters natively
    if (!data || (!data.ok && data.status !== 'success')) {
      var errMsg = data && data.error ? data.error : 'unknown';
      $target.html('<div class="text-danger">Error: ' + errMsg + '</div>');
      return;
    }

    // 3. Render Success Message Output
    var $div = $('<div class="mt-2 alert alert-success"></div>');
    var $p = $('<p class="mb-0"></p>').text(data.file || 'Yearly calculations compiled successfully!');
    
    $div.append($p);
    $target.append($div);

  }).fail(function() {
    // Handle offline structural timeouts cleanly
    $btn.prop('disabled', false).text(origText);
    $target.html('<div class="text-danger">Network error communicating with the system.</div>');
  });
});