# hxcgnat

CGNAT com BPA (Bloco de Portas Alocadas) sobre VPP/DPDK.

Alvo: 100 Gbps+, 200k+ assinantes, entregue como appliance Debian.

## Layout

```
docs/architecture.md      decisoes de projeto e o problema de sharding
ref/bpa_model.py          especificacao executavel do mapeamento BPA
ref/validate.py           round-trip exaustivo + planejamento de capacidade
ref/check_magic.py        prova do reciprocal usado no datapath
src/plugins/bpa/          plugin VPP out-of-tree
appliance/                ISO, tuning de kernel, systemd
lab/                      topologia de teste
```

## Verificar o modelo

```sh
cd ref && python3 validate.py && python3 check_magic.py
```

Nao precisa de root nem de compilador. O plugin em C deve reproduzir estes
resultados bit a bit; qualquer mudanca no mapeamento passa por aqui primeiro.

## Ambiente de build (pendente)

A maquina de desenvolvimento precisa de root para: instalar toolchain e
`vpp-dev`, reservar hugepages e fazer bind de NIC no DPDK. Ver docs/architecture.md.
