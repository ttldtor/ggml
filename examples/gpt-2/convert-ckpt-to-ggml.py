# Convert a model checkpoint to a ggml compatible file
#
# Load the model using TensorFlow.
# Iterate over all variables and write them to a binary file.
#
# For each variable, write the following:
#   - Number of dimensions (int)
#   - Name length (int)
#   - Dimensions (int[n_dims])
#   - Name (char[name_length])
#   - Data (float[n_dims])
#
# By default, the bigger matrices are converted to 16-bit floats.
# This can be disabled by adding the "use-f32" CLI argument.
#
# At the start of the ggml file we write the model parameters
# and vocabulary.
#

import sys
import json
import struct
import numpy as np
import tensorflow as tf

# ref: https://github.com/openai/gpt-2/blob/master/src/encoder.py
def bytes_to_unicode():
    """
    Returns list of utf-8 byte and a corresponding list of unicode strings.
    The reversible bpe codes work on unicode strings.
    This means you need a large # of unicode characters in your vocab if you want to avoid UNKs.
    When you're at something like a 10B token dataset you end up needing around 5K for decent coverage.
    This is a signficant percentage of your normal, say, 32K bpe vocab.
    To avoid that, we want lookup tables between utf-8 bytes and unicode strings.
    And avoids mapping to whitespace/control characters the bpe code barfs on.
    """
    bs = list(range(ord("!"), ord("~")+1))+list(range(ord("¡"), ord("¬")+1))+list(range(ord("®"), ord("ÿ")+1))
    cs = bs[:]
    n = 0
    for b in range(2**8):
        if b not in bs:
            bs.append(b)
            cs.append(2**8+n)
            n += 1
    cs = [chr(n) for n in cs]
    return dict(zip(bs, cs))

if len(sys.argv) < 2:
    print("Usage: convert-ckpt-to-ggml.py dir-model [use-f32]\n")
    sys.exit(1)

# output in the same directory as the model
dir_model = sys.argv[1]
fname_out = sys.argv[1] + "/ggml-model.bin"

with open(dir_model + "/encoder.json", "r") as f:
    encoder = json.load(f)

with open(dir_model + "/hparams.json", "r") as f:
    hparams = json.load(f)

# use 16-bit or 32-bit floats
use_f16 = True
if len(sys.argv) > 2:
    use_f16 = False
    fname_out = sys.argv[1] + "/ggml-model-f32.bin"

list_vars = tf.train.list_variables(dir_model)

fout = open(fname_out, "wb")

fout.write(struct.pack("i", 0x67676d6c)) # magic: ggml in hex
fout.write(struct.pack("i", hparams["n_vocab"]))
fout.write(struct.pack("i", hparams["n_ctx"]))
fout.write(struct.pack("i", hparams["n_embd"]))
fout.write(struct.pack("i", hparams["n_head"]))
fout.write(struct.pack("i", hparams["n_layer"]))
fout.write(struct.pack("i", use_f16))

byte_encoder = bytes_to_unicode()
byte_decoder = {v:k for k, v in byte_encoder.items()}

fout.write(struct.pack("i", len(encoder)))
for key in encoder:
    text = bytearray([byte_decoder[c] for c in key]).decode('utf-8', errors='replace').encode('utf-8')
    fout.write(struct.pack("i", len(text)))
    fout.write(text)

for name, shape in list_vars:
    print("Processing variable: " + name + " with shape: ", shape)

    data = tf.train.load_variable(dir_model, name).squeeze()
    n_dims = len(data.shape);

    # ftype == 0 -> float32, ftype == 1 -> float16
    ftype = 0;
    if use_f16:
        # match name:
        #  "model/wte"
        #  "model/h.*/attn/c_attn/w"
        #  "model/h.*/attn/c_proj/w"
        #  "model/h.*/mlp/c_fc/w"
        #  "model/h.*/mlp/c_proj/w"
        if name == "model/wte" or name[-2:] == "/w":
            print("  Converting to float16")
            data = data.astype(np.float16)
            ftype = 1

    # for efficiency - transpose the projection matrices
    if name[-13:] == "/mlp/c_proj/w":
        print("  Transposing")
        data = data.transpose()

    # header
    str = name.encode('utf-8')
    fout.write(struct.pack("iii", n_dims, len(str), ftype))
    for i in range(n_dims):
        fout.write(struct.pack("i", data.shape[n_dims - 1 - i]))
    fout.write(str);

    # data
    data.tofile(fout)

fout.close()

print("Done. Output file: " + fname_out)
print("")
