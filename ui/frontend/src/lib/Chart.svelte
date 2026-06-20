<script>
  import { onMount, onDestroy } from 'svelte'
  import uPlot from 'uplot'
  import 'uplot/dist/uPlot.min.css'
  let { data, series, height = 220 } = $props()
  let el = $state(null), plot = null, w = $state(600)
  function opts() {
    return { width: w, height, series,
      scales: { x: { time: true } },
      axes: [{}, {}],
      legend: { live: true } }
  }
  onMount(() => {
    plot = new uPlot(opts(), data, el)
    const ro = new ResizeObserver(() => { w = el.clientWidth; plot?.setSize({ width: w, height }) })
    ro.observe(el)
    return () => ro.disconnect()
  })
  onDestroy(() => plot?.destroy())
  // re-feed data on change
  $effect(() => { if (plot && data) plot.setData(data) })
</script>
<div bind:this={el} bind:clientWidth={w} class="chart"></div>
<style>.chart{width:100%}</style>
