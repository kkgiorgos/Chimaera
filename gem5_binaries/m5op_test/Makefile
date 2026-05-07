GEM5 = /home/kkgiorgos/University/Diploma/Chimaera/gem5

all: test_bridge

test_bridge:
	g++ main.c \
		-no-pie \
		-I"$(GEM5)/include" \
		-I"$(GEM5)/util/m5/src" \
		-L"$(GEM5)/util/m5/build/x86/out" \
		-lm5 \
		-o test_bridge

clean:
	rm test_bridge
