/* linker script of the QEMU nested-fault test (tools/el2-qemu-test): the shim's layout (shim.ld) at the virt machine's RAM base; the EL2 stack goes last in .bss */
ENTRY(_start)
SECTIONS
{
	. = 0x40000000;
	.text : { *(.text.head) *(.text*) }
	.rodata : { *(.rodata*) }
	.data : { *(.data*) }
	. = ALIGN(16);
	_bss_start = .;
	.bss : { *(.bss*) *(COMMON) *(.el2stack) PROVIDE(_el2_stack_top = .); }
	. = ALIGN(16);
	_bss_end = .;
	_end = .;
	/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
