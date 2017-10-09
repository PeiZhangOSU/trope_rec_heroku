$(function() {
  console.log('time to search!');
  let $trope_search = $("#trope_search");
  let $search_results = $("#search_results");
  let tropes = null;

  $($trope_search).change(function() {
    let value = $(this).val();
    console.log('saw val', value);
    if (value.length > 1) {
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
