$(function() {
  console.log('time to search!');
  let $trope_search = $("#trope_search");
  let $search_results = $("#search_results");
  let tropes = null;
  let $user_tropes = $('#user_tropes')

  $($trope_search).change(function() {
    let value = $(this).val();
    console.log('saw val', value);
    if (value.length > 2) {
      console.log('searching tropes')
      let re = new RegExp(value, "i");
      let matches = tropes.filter(function(trope) {
        return trope.match(re)
      })
      console.log('found matches', matches);
      $search_results.empty();
      matches.forEach(function(match){
        let match_li = document.createElement('li');
        match_li.innerHTML = match;
        $search_results.append(match_li);
        $(match_li).on('click', function() {
          console.log('you clicked', this);
          $user_tropes.append(', ' + this.innerHTML)
        })
      })

    }
    else {
      $search_results.empty()
    }
  }).keyup(function() {
    $(this).change();
  })

  $.get('api/1/tropes', function(data) {
    console.log('got data', data);
    tropes = data['tropes'];
  })
});
