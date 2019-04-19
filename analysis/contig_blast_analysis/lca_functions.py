##########
# Determine the Lowest Common Ancestor (LCA) of a set of taxonomic IDs
##########

import pandas as pd
from ete3 import NCBITaxa
import boto3
import tempfile
import os


# load ncbi taxonomy database
ncbi = NCBITaxa()
update_tax_database = False
if update_tax_database:
    ncbi.update_taxonomy_database()



##
## Find the lowest common ancestor (LCA) for a given set of taxonomic groups
##
def get_lca (taxids, tax_col="taxid", query_col="query"):
    # taxids should be a list of integers, e.g. [1, 12392, 178688] or
    #   a data frame with taxids stored in the `tax_col` column
    # returns an integer if input was a list, or a data frame if input
    #   was a data frame
    if isinstance(taxids, pd.DataFrame):
        lca = taxids[[query_col, tax_col]].iloc[[0]]
        lca.iloc[0, 1] = get_lca(taxids[tax_col].tolist())
    else:
        if (len(set(taxids)) <= 1):
            lca = taxids[0]
        else:
            tree = ncbi.get_topology(taxids)
            lca = tree.get_tree_root().taxid
    return (lca)


##
## Select taxonomic IDs to perfrom LCA analysis on based on BLAST results
##
def select_taxids_for_lca (df, return_taxid_only=True, ident_cutoff=0, align_len_cutoff=0, bitscore_cutoff=0):
    # df should be a pandas dataframe
    # remove blast hits where identity < ident_cutoff*max(identity) AND
    # align_length < align_len_cutoff*max(align_length) AND
    # bitscore < bitscore_cutoff*max(bitscore)
    if (len(df.index)>1):
        df = df[df["identity"]>=(ident_cutoff*df["identity"].max())]
        df = df[df["align_length"]>=(align_len_cutoff*df["align_length"].max())]
        df = df[df["bitscore"]>=(bitscore_cutoff*df["bitscore"].max())]
    if (return_taxid_only):
        return (list(set(df["taxid"])))
    else:
        return (df)


##
## Read in BLAST results stored in tab-delimited files as a pandas data frame
##
def parse_blast_file (fpath, sep="\t", comment=None, blast_type="nt", col_names="auto"):
    # col_names:
    #   'auto' if the column headings should be auto-populated based on the # of columns
    #   '[a, b, c]' if the column headings should be set to a, b, c
    #   'None' if column headings should not be changed after reading in the file
    df = pd.read_csv(fpath, sep=sep, header=None, comment=comment)
    if (col_names=="auto"):
        # blast column headings:
        col_headings = ["query", "subject", "identity", "align_length", "mismatches", 
        "gaps", "qstart", "qend", "sstart", "send", "evalue", "bitscore", "taxid", 
        "sci_name", "common_name", "subject_title"]
        df.columns = col_headings[:len(df.columns)]
    elif (col_names is not None):
        df.columns = col_names
    df = df.assign(blast_type=blast_type)
    return (df)


##
## Split an s3://bucket/path string into the bucket name and the path
##
def split_s3_path (s3_path):
    s3_split = os.path.normpath(s3_path).split(os.sep)
    bucket_name = s3_split[1]
    s3_path = '/'.join(s3_split[2:])
    return bucket_name, s3_path

##
## Upload pandas dataframe to S3
##
def df_to_s3 (obj, s3path):
    s3 = boto3.resource('s3')
    client = boto3.client('s3')
    out_file = tempfile.NamedTemporaryFile()
    obj.to_csv(out_file.name, sep="\t", index=False)
    data = open(out_file.name, "rb")
    s3_bucket_name, s3_path = split_s3_path(s3path)
    s3.Bucket(s3_bucket_name).put_object(Key=s3_path, Body=data)

##
## Download file from S3 and return the local path to this file
##
def download_s3_file (s3path):
    s3 = boto3.resource('s3')
    client = boto3.client('s3')
    s3_bucket_name, s3_path = split_s3_path(s3path)
    fpath = client.get_object(Bucket=s3_bucket_name, Key=s3_path)['Body']
    return fpath