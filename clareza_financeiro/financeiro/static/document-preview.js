document.addEventListener('DOMContentLoaded',()=>{
  const file=document.querySelector('#expense-form #id_document');
  if(!file)return;
  const image=document.querySelector('#preview-image');
  const pdf=document.querySelector('#preview-pdf');
  const placeholder=document.querySelector('#preview-placeholder');
  const name=document.querySelector('#preview-name');
  const open=document.querySelector('#preview-open');
  const panel=document.querySelector('.document-preview');
  let previewUrl=null;

  const clear=()=>{
    image.hidden=true;
    pdf.hidden=true;
    open.hidden=true;
    image.removeAttribute('src');
    pdf.removeAttribute('src');
    open.removeAttribute('href');
    if(previewUrl)URL.revokeObjectURL(previewUrl);
    previewUrl=null;
    placeholder.hidden=false;
    name.textContent='Confira o documento enquanto preenche a despesa.';
  };
  const update=()=>{
    clear();
    const selected=file.files[0];
    const replacementName=document.querySelector('#replacement-name');
    if(replacementName)replacementName.textContent=selected?`Novo arquivo: ${selected.name}. Será substituído ao salvar.`:'';
    const savedStatus=document.querySelector('.saved-attachment');
    if(savedStatus)savedStatus.textContent=selected?'Anexo salvo disponível até confirmar a substituição.':'✓ Anexo salvo. Ele será mantido ao salvar a despesa.';
    const savedUrl=panel.dataset.savedUrl;
    if(!selected&&!savedUrl){placeholder.textContent='Selecione um PDF, JPG ou PNG para visualizar aqui.';return;}
    const documentName=selected?selected.name:panel.dataset.savedName;
    const extension=documentName.split('.').pop().toLowerCase();
    const type={pdf:'application/pdf',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg'}[extension];
    if(!type||(selected&&selected.size>10*1024*1024)){
      placeholder.textContent='A prévia aceita PDF, JPG ou PNG de até 10 MB.';
      return;
    }
    // A prévia usa o arquivo local e permanece após o seletor ser limpo pela extração.
    if(selected)previewUrl=URL.createObjectURL(new Blob([selected],{type}));
    const source=previewUrl||savedUrl;
    name.textContent=selected?documentName:'Anexo salvo';
    open.href=source;
    open.hidden=false;
    if(type==='application/pdf'){
      pdf.src=source;
      pdf.hidden=false;
      placeholder.textContent='Se o PDF não aparecer, use “Abrir documento em outra aba”.';
    }else{
      image.src=source;
      image.hidden=false;
      placeholder.hidden=true;
    }
  };
  image.addEventListener('error',()=>{
    image.hidden=true;
    placeholder.hidden=false;
    placeholder.textContent='Não foi possível visualizar esta imagem. Você pode continuar preenchendo a despesa.';
  });
  file.addEventListener('change',update);
  window.addEventListener('pagehide',event=>{if(!event.persisted)clear();});
  update();
});
