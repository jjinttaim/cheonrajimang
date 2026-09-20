// Backend and data labels may still carry Korean middle dots; the UI shows commas instead.
export const plain=text=>String(text??'').replace(/·/g,', ');
