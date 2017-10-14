$(function() {
  console.log('time to search!');
  let $trope_search_plot = $("#trope_search_plot");
  let $search_results_plot = $("#search_results_plot");
  let tropes = null;

  $($trope_search_plot).change(function() {
    let value = $(this).val();
    console.log('saw val', value);
    if (value.length > 2) {
      console.log('searching tropes')
      let re = new RegExp(value, "i");
      let matches = tropes.filter(function(trope) {
        return trope.match(re)
      })
      console.log('found matches', matches);
      $search_results_plot.empty();
      matches.forEach(function(match){
        let match_li = document.createElement('li');
        match_li.innerHTML = match;
        $search_results_plot.append(match_li);
        $(match_li).click(function() {
          console.log('you clicked', this);
          $trope_search_plot.val(this.innerHTML)
        })
      })

    }
    else {
      $search_results_plot.empty()
    }
  }).keyup(function() {
    $(this).change();
  })

  $.get('api/1/tropes', function(data) {
    console.log('got data', data);
    tropes = data['tropes'];
  })
});
